"""Local browser interface for the SQL Server reviewer."""

from difflib import unified_diff
import json
import os
from ipaddress import ip_address
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory

from defusedxml.common import DefusedXmlException
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from xml.etree.ElementTree import ParseError

from deployment import configured_profile
from local_settings import form_settings
from github_review import DraftError, create_draft, review_target, source_digest
from reviewer import (
    AIConfigurationError, ai_review, catalog_findings, collect,
    plan_findings, render, request_findings, static_findings,
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secrets.token_hex(32),
    DEPLOYMENT_PROFILE=configured_profile(),
    LOCAL_SETTINGS=(None, {}, ""),
    TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
    SESSION_COOKIE_SAMESITE="Strict",
    MAX_CONTENT_LENGTH=22 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=1024 * 1024,
)
PROVIDERS = dict(azure="Azure OpenAI", openai="OpenAI", compatible="OpenAI-compatible")
PLAN_LIMIT = 20 * 1024 * 1024
SQL_LIMIT = 200_000


def provider_choices(profile):
    if profile is None:
        return PROVIDERS
    return {key: entry["label"] for key, entry in profile.get("ai", {}).items()}


@app.before_request
def snapshot_settings():
    local, bindings, revision = app.config["LOCAL_SETTINGS"]
    managed = app.config["DEPLOYMENT_PROFILE"]
    g.profile = managed if managed is not None else local
    g.bindings = bindings if managed is None else {}
    g.settings_revision = revision if managed is None else ""


def setting(name, default=None):
    return g.bindings[name] if name in g.bindings else os.getenv(name, default)


def deployment_status(profile):
    sql_env = profile["sql_connection_env"] if profile else "SQLSERVER_CONNECTION_STRING"
    legacy_ai = {
        "azure": {"endpoint_env": "AZURE_OPENAI_ENDPOINT", "model_env": "AZURE_OPENAI_DEPLOYMENT",
                  "key_env": "AZURE_OPENAI_API_KEY"},
        "openai": {"model_env": "OPENAI_MODEL", "key_env": "OPENAI_API_KEY"},
        "compatible": {"endpoint_env": "SQL_REVIEW_AI_BASE_URL", "model_env": "SQL_REVIEW_AI_MODEL"},
    }
    models = profile.get("ai", {}) if profile is not None else legacy_ai
    ready = {
        choice for choice, options in models.items()
        if all(setting(options[key], "").strip()
               for key in ("endpoint_env", "model_env", "key_env") if key in options)
    }
    github = profile.get("github") if profile is not None else None
    if profile is None:
        try:
            modules = json.loads(setting("SQL_REVIEW_GITHUB_MODULES", "{}"))
        except ValueError:
            modules = {}
        if isinstance(modules, dict):
            github = {"repository": setting("SQL_REVIEW_GITHUB_REPOSITORY"),
                      "modules": modules, "token_env": "SQL_REVIEW_GITHUB_TOKEN"}
    drafts = bool(github and github["repository"] and github["modules"]
                  and setting(github["token_env"], "").strip())
    return dict(sql_env=sql_env, sql=bool(setting(sql_env, "").strip()), ready=ready, drafts=drafts)


def review_form(values, upload):
    profile = g.profile
    choices = provider_choices(profile)
    provider = values["provider"]
    if provider and provider not in choices:
        raise ValueError("Choose an AI provider from the list.")

    evidence, notes = {}, []
    if values["source"] == "database":
        label = values["module"].strip()
        parts = label.split(".")
        if len(parts) != 2 or any(not part or len(part) > 128 for part in parts):
            raise ValueError("Enter an unquoted SQL object name such as dbo.usp_GetDeals.")
        connection_env = profile["sql_connection_env"] if profile else "SQLSERVER_CONNECTION_STRING"
        connection = setting(connection_env)
        if not connection:
            raise ValueError(f"Set {connection_env} on the server to review database objects.")
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
            if profile is None:
                proposal = ai_review(definition, findings, evidence, provider)
            else:
                model = profile["ai"][provider]
                options = {"lookup": setting} if g.bindings else {}
                proposal = ai_review(definition, findings, evidence, model["provider"], config=model, **options)
            notes.append(f"AI provider used for proposed T-SQL: {choices[provider]}.")
        except AIConfigurationError as exc:
            ai_error = str(exc)
        except Exception:
            ai_error = f"{choices[provider]} could not complete the review. Check the provider configuration and try again."
        if ai_error:
            notes.append(ai_error)

    snippets = re.findall(r"^`{3}(?:sql|tsql|t-sql)[ \t]*\r?\n(.*?)^`{3}[ \t]*$", proposal,
                          flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return dict(
        original_sql=definition, candidate_sql=snippets[0] if len(snippets) == 1 else "",
        findings=findings, evidence=evidence, notes=notes, proposal=proposal,
        ai_error=ai_error, report=render(label, findings, evidence, notes, proposal),
    )


@app.route("/", methods=["GET", "POST"])
def index():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    profile = g.profile
    github = profile.get("github") if profile else None
    values = dict(source="editor", sql="", module="", spid="", provider="")
    result, error = None, ""
    status = 200
    if request.method == "POST":
        check_csrf()
        for key in values:
            values[key] = request.form.get(key, values[key])
        try:
            result = review_form(values, request.files.get("plan"))
            if result["proposal"] and values["source"] == "database" and (profile is None or github):
                module = values["module"].strip()
                try:
                    repo, base, path = review_target(module, github)
                except DraftError:
                    pass
                else:
                    result["draft_target"] = f"{repo}/{path} ({base})"
                    result["draft_token"] = draft_signer().dumps({
                        "module": module, "original": source_digest(result["original_sql"]),
                        "titles": sorted({finding.title for finding in result["findings"]}),
                    })
        except (ParseError, DefusedXmlException):
            error, status = "The execution plan is not valid SQL Server Showplan XML.", 400
        except ValueError as exc:
            error, status = str(exc), 400
        except Exception:
            error, status = "The review could not finish. Check SQL Server connectivity and permissions.", 500
    return render_template(
        "index.html", csrf=session["csrf"], values=values, providers=provider_choices(profile), profile=profile,
        result=result, error=error, setup=deployment_status(profile),
    ), status


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not ip_address(request.remote_addr or "0.0.0.0").is_loopback:
        abort(403)
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    managed = app.config["DEPLOYMENT_PROFILE"] is not None
    saved = request.args.get("saved") == "1"
    error = ""
    if request.method == "POST":
        check_csrf()
        if managed:
            abort(403)
        try:
            if request.form.get("action") == "reset":
                app.config["LOCAL_SETTINGS"] = (None, {}, secrets.token_hex(16))
            else:
                profile, bindings = form_settings(request.form, g.bindings)
                app.config["LOCAL_SETTINGS"] = (profile, bindings, secrets.token_hex(16))
            return redirect(url_for("settings", saved="1"), code=303)
        except ValueError as exc:
            error = str(exc)
    response = app.make_response((render_template(
        "settings.html", csrf=session["csrf"], managed=managed, profile=g.profile,
        model_fields={key: value for key, value in g.bindings.items()
                      if key.endswith(("_NAME", "_ENDPOINT", "_PROVIDER"))},
        saved=saved, error=error, status=deployment_status(g.profile),
    ), 400 if error else 200))
    response.headers["Cache-Control"] = "no-store"
    return response


def draft_signer():
    return URLSafeTimedSerializer(app.secret_key, salt=f"draft:{session['csrf']}:{g.settings_revision}")


def check_csrf():
    csrf = session.get("csrf", "")
    if not csrf or not secrets.compare_digest(request.form.get("csrf", "").encode(), csrf.encode()):
        abort(403)


@app.post("/compare")
def compare():
    check_csrf()
    original = request.form.get("original", "")
    candidate = request.form.get("candidate", "")
    if max(len(original), len(candidate)) > SQL_LIMIT:
        return jsonify(error="Each definition must be 200,000 characters or fewer."), 400
    diff = unified_diff(original.splitlines(), candidate.splitlines(),
                        fromfile="Original", tofile="Candidate", lineterm="")
    return jsonify(diff="\n".join(diff))


@app.post("/draft")
def draft():
    check_csrf()
    try:
        review = draft_signer().loads(request.form.get("draft_token", ""), max_age=3600)
    except BadSignature:
        return jsonify(error="The review expired or is invalid. Run a new live review."), 400

    candidate = request.form.get("candidate", "").strip()
    if not candidate or len(candidate) > SQL_LIMIT:
        return jsonify(error="Enter a candidate between 1 and 200,000 characters."), 400
    profile = g.profile
    if profile is not None and not profile.get("github"):
        return jsonify(error="Draft PRs are disabled for this deployment."), 403
    options = {"config": profile["github"]} if profile is not None else {}
    if g.bindings:
        options["lookup"] = setting
    try:
        url = create_draft(review["module"], review["original"], candidate + "\n", review["titles"], **options)
    except DraftError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(url=url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
