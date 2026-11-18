"""Create a draft review for an explicitly mapped SQL module."""

import base64
import hashlib
import json
import os
import re
from urllib.parse import quote
from uuid import uuid4

import httpx


class DraftError(ValueError):
    pass


def source_digest(sql: str) -> str:
    normalized = sql.removeprefix("\ufeff").replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def review_target(module: str) -> tuple[str, str, str]:
    repo = os.getenv("SQL_REVIEW_GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo):
        raise DraftError("Configure SQL_REVIEW_GITHUB_REPOSITORY on the server.")
    try:
        mapping = json.loads(os.getenv("SQL_REVIEW_GITHUB_MODULES", "{}"))
        path = mapping.get(module)
    except (ValueError, AttributeError):
        raise DraftError("Set SQL_REVIEW_GITHUB_MODULES to a JSON object.") from None
    if not isinstance(path, str) or not path.lower().endswith(".sql") or any(
        not re.fullmatch(r"[\w. -]+", part) or part in {".", ".."}
        for part in path.split("/")
    ):
        raise DraftError("Map this database object to an existing .sql file on the server.")
    return repo, os.getenv("SQL_REVIEW_GITHUB_BASE", "main"), path


def create_draft(module: str, original: str, candidate: str, titles: list[str]) -> str:
    repo, base, path = review_target(module)
    token = os.getenv("SQL_REVIEW_GITHUB_TOKEN")
    if not token:
        raise DraftError("Configure a repository-scoped SQL_REVIEW_GITHUB_TOKEN on the server.")
    if source_digest(candidate) == original:
        raise DraftError("The candidate matches the live definition. Edit it before creating a draft.")

    branch = f"sql-review/{uuid4().hex}"
    endpoint = f"https://api.github.com/repos/{repo}"
    with httpx.Client(base_url=endpoint, timeout=20, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }) as client:
        try:
            head = client.get(f"git/ref/heads/{quote(base, safe='/')}")
            head.raise_for_status()
            sha = head.json()["object"]["sha"]
            file_url = f"contents/{quote(path, safe='/')}"
            current = client.get(file_url, params={"ref": sha})
            current.raise_for_status()
            item = current.json()
            if item["type"] != "file" or item["encoding"] != "base64":
                raise DraftError("The mapped file must be a UTF-8 SQL file.")
            stored = base64.b64decode(item["content"]).decode("utf-8")
            if source_digest(stored) != original:
                raise DraftError("The mapped GitHub file differs from the reviewed live definition. Reconcile the two before opening a draft.")
        except (httpx.HTTPError, KeyError, TypeError, UnicodeError):
            raise DraftError("Could not read the configured GitHub branch or SQL file. Check the mapping and repository access.") from None

        try:
            ref = client.post("git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
            ref.raise_for_status()
            edit = client.put(file_url, json={
                "message": f"Propose SQL rewrite for {module}",
                "content": base64.b64encode(candidate.encode()).decode(),
                "sha": item["sha"], "branch": branch,
            })
            edit.raise_for_status()
            summary = "\n".join(f"- {title}" for title in titles)
            body = (
                "Candidate rewrite generated from a live SQL Server review and submitted for human review.\n\n"
                "### Review findings\n" + (summary or "No deterministic findings matched.") + "\n\n"
                "### Validation checklist\n"
                "- [ ] Verify result shape, values, multiplicity, ordering, and write side effects.\n"
                "- [ ] Compare actual plans, logical reads, CPU, and elapsed time with representative parameters in staging.\n"
                "- [ ] Confirm deployment and rollback steps before merge."
            )
            pull = client.post("pulls", json={
                "title": f"Review SQL rewrite for {module}", "head": branch, "base": base,
                "body": body, "draft": True,
            })
            pull.raise_for_status()
            url = pull.json()["html_url"]
            if not re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/[0-9]+", url, re.IGNORECASE):
                raise DraftError(f"Check GitHub for the draft associated with branch {branch}.")
            return url
        except (httpx.HTTPError, KeyError, TypeError):
            raise DraftError(f"GitHub did not confirm draft creation. Check branch {branch} and the repository's pull requests before retrying.") from None
