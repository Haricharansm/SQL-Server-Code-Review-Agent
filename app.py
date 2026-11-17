"""Local browser interface for the SQL Server reviewer."""

import os
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory

from defusedxml.common import DefusedXmlException
from flask import Flask, abort, render_template, request, session
from xml.etree.ElementTree import ParseError

from reviewer import (
    AI_PROVIDERS, AIConfigurationError, ai_review, catalog_findings, collect,
    plan_findings, render, request_findings, static_findings,
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secrets.token_hex(32),
    TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
    SESSION_COOKIE_SAMESITE="Strict",
    MAX_CONTENT_LENGTH=22 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=1024 * 1024,
)
PROVIDERS = dict(azure="Azure OpenAI", openai="OpenAI", compatible="OpenAI-compatible")
PLAN_LIMIT = 20 * 1024 * 1024
SQL_LIMIT = 200_000


def review_form(values, upload):
    provider = values["provider"]
    if provider not in ("", *AI_PROVIDERS):
        raise ValueError("Choose an AI provider from the list.")

    evidence, notes = {}, []
    if values["source"] == "database":
        label = values["module"].strip()
        parts = label.split(".")
        if len(parts) != 2 or any(not part or len(part) > 128 for part in parts):
            raise ValueError("Enter an unquoted SQL object name such as dbo.usp_GetDeals.")
        connection = os.getenv("SQLSERVER_CONNECTION_STRING")
        if not connection:
            raise ValueError("Set SQLSERVER_CONNECTION_STRING on the server to review database objects.")
        live = values["spid"].strip()
        if live and (not re.fullmatch(r"[0-9]{1,5}", live) or not 1 <= int(live) <= 32767):
            raise ValueError("Enter a session ID from 1 to 32767.")
        definition, evidence, notes = collect(connection, *parts, int(live) if live else None)
        if live:
            notes.append("Verify that the supplied session was running the intended statement.")
    elif values["source"] == "editor":
        label = "SQL editor"
        definition = values["sql"]
        if not definition.strip():
            raise ValueError("Paste SQL or load the example before running a review.")
    else:
        raise ValueError("Choose SQL editor or database object.")

    if len(definition) > SQL_LIMIT:
        raise ValueError("The SQL definition must be 200,000 characters or fewer.")
    findings = static_findings(definition)
    if evidence:
        findings.extend(catalog_findings(evidence["dependencies"], evidence["objects"]))
        findings.extend(request_findings(evidence["live_request"]))
    if upload and upload.filename:
        if Path(upload.filename).suffix.lower() not in {".sqlplan", ".xml"}:
            raise ValueError("Upload a SQL Server .sqlplan or .xml file.")
        content = upload.stream.read(PLAN_LIMIT + 1)
        if len(content) > PLAN_LIMIT:
            raise ValueError("The execution plan must be 20 MiB or smaller.")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "upload.sqlplan"
            path.write_bytes(content)
            findings.extend(plan_findings(path))
        notes.append("Verify that the supplied plan matches the reviewed statement and parameter set.")

    proposal, ai_error = "", ""
    if provider:
        try:
            proposal = ai_review(definition, findings, evidence, provider)
            notes.append(f"AI provider used for proposed T-SQL: {PROVIDERS[provider]}.")
        except AIConfigurationError as exc:
            ai_error = str(exc)
        except Exception:
            ai_error = f"{PROVIDERS[provider]} could not complete the review. Check the provider configuration and try again."
        if ai_error:
            notes.append(ai_error)

    snippets = re.findall(r"^`{3}(?:sql|tsql|t-sql)[ \t]*\r?\n(.*?)^`{3}[ \t]*$", proposal,
                          flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return dict(
        candidate_sql="\n\n".join(snippets), findings=findings, evidence=evidence, notes=notes, proposal=proposal,
        ai_error=ai_error, report=render(label, findings, evidence, notes, proposal),
    )


@app.route("/", methods=["GET", "POST"])
def index():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    values = dict(source="editor", sql="", module="", spid="", provider="")
    result, error = None, ""
    status = 200
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", "").encode(), session["csrf"].encode()):
            abort(403)
        for key in values:
            values[key] = request.form.get(key, values[key])
        try:
            result = review_form(values, request.files.get("plan"))
        except (ParseError, DefusedXmlException):
            error, status = "The execution plan is not valid SQL Server Showplan XML.", 400
        except ValueError as exc:
            error, status = str(exc), 400
        except Exception:
            error, status = "The review could not finish. Check SQL Server connectivity and permissions.", 500
    return render_template(
        "index.html", csrf=session["csrf"], values=values, providers=PROVIDERS,
        result=result, error=error,
    ), status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
