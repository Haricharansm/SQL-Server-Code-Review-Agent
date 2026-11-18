"""Load a server-side deployment profile without embedding credentials."""

import json
import os
from pathlib import Path
import re


def environment_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Z_][A-Z_0-9]*", value)


def load_profile(path):
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Could not read the deployment profile as JSON.") from None
    if not isinstance(profile, dict) or set(profile) - {"name", "sql_connection_env", "ai", "github"}:
        raise ValueError("Invalid deployment profile fields.")
    if not isinstance(profile.get("name"), str) or not profile["name"].strip():
        raise ValueError("Set a deployment profile name.")
    if not environment_name(profile.get("sql_connection_env")):
        raise ValueError("Set sql_connection_env to an environment variable name.")

    models = profile.get("ai", {})
    if not isinstance(models, dict):
        raise ValueError("Set ai to an object keyed by model choice.")
    required = {
        "azure": {"model_env", "endpoint_env", "key_env"},
        "openai": {"model_env", "key_env"},
        "compatible": {"model_env", "endpoint_env"},
    }
    optional = {"azure": {"version_env"}, "openai": set(), "compatible": {"key_env"}}
    for option, model in models.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", option) or not isinstance(model, dict):
            raise ValueError("Invalid AI choice ID or settings.")
        provider = model.get("provider")
        if not isinstance(provider, str) or provider not in required:
            raise ValueError("Choose azure, openai or compatible for each AI choice.")
        if not isinstance(model.get("label"), str) or not model["label"].strip():
            raise ValueError("Set a display label for each AI choice.")
        variables = set(model) - {"provider", "label"}
        if not required[provider] <= variables <= required[provider] | optional[provider] or any(
            not environment_name(model[key]) for key in variables
        ):
            raise ValueError("Set the required environment variable references for each AI choice.")

    github = profile.get("github")
    if github is not None:
        if not isinstance(github, dict) or set(github) != {"repository", "base", "modules", "token_env"}:
            raise ValueError("Set repository, base, modules and token_env for GitHub drafts.")
        if not isinstance(github["repository"], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", github["repository"]
        ) or not isinstance(github["base"], str) or not github["base"].strip():
            raise ValueError("Set the GitHub repository and base branch.")
        if not environment_name(github["token_env"]) or not isinstance(github["modules"], dict):
            raise ValueError("Set a GitHub token environment name and module mapping.")
    return profile


def configured_profile():
    path = os.getenv("SQL_REVIEW_PROFILE")
    return load_profile(path) if path else None
