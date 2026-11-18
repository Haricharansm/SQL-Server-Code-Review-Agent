"""Build temporary settings from the single-user Studio form."""

import re
from urllib.parse import urlsplit


def form_settings(form, previous):
    def field(name):
        return form.get(name, "").strip()

    def secret(field_name, key, *, preserve=True):
        return field(field_name) or (previous.get(key, "") if preserve else "")

    profile = {"name": field("name") or "Local Studio", "sql_connection_env": "STUDIO_LOCAL_SQL", "ai": {}}
    bindings = {"STUDIO_LOCAL_SQL": "" if field("clear_connection") else secret("connection", "STUDIO_LOCAL_SQL")}

    for slot in range(1, 4):
        provider = field(f"provider_{slot}")
        if not provider:
            continue
        if provider not in ("azure", "openai", "compatible"):
            raise ValueError(f"Choose a supported provider for model {slot}.")
        label, model, endpoint = (field(f"{name}_{slot}") for name in ("label", "model", "endpoint"))
        if not label or not model:
            raise ValueError(f"Enter a display name and model or deployment name for model {slot}.")
        prefix = f"STUDIO_MODEL_{slot}"
        same_target = (provider == previous.get(prefix + "_PROVIDER")
                       and endpoint == previous.get(prefix + "_ENDPOINT", ""))
        key = secret(f"key_{slot}", prefix + "_KEY", preserve=same_target)
        if provider != "compatible" and not key:
            raise ValueError(f"Enter the API key for model {slot}.")
        choice = {"label": label, "provider": provider, "model_env": prefix + "_NAME"}
        if provider != "openai":
            url = urlsplit(endpoint)
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            if not url.hostname or url.username or url.password or url.query or url.fragment or (
                url.scheme != "https" and not (provider == "compatible" and url.scheme == "http" and local)
            ):
                raise ValueError(f"Enter an HTTPS endpoint for model {slot}, or a loopback HTTP compatible endpoint.")
            choice["endpoint_env"] = prefix + "_ENDPOINT"
        if key:
            choice["key_env"] = prefix + "_KEY"
        bindings.update({prefix + "_NAME": model, prefix + "_KEY": key,
                         prefix + "_ENDPOINT": endpoint, prefix + "_PROVIDER": provider})
        profile["ai"][f"model-{slot}"] = choice

    repo = field("repository")
    base = field("base") or "main"
    if repo:
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Enter the GitHub repository as owner/repository.")
        mappings = {}
        for line in field("modules").splitlines():
            if not line.strip():
                continue
            module, _, path = (part.strip() for part in line.partition("="))
            if not re.fullmatch(r"[^.\s]+\.[^.\s]+", module) or module in mappings or not path.lower().endswith(".sql") or any(
                not re.fullmatch(r"[\w. -]+", part) or part in {".", ".."} for part in path.split("/")
            ):
                raise ValueError("Enter one unique module mapping per line as schema.object=path/file.sql.")
            mappings[module] = path
        token = secret("github_token", "STUDIO_GITHUB_TOKEN", preserve=repo == previous.get("STUDIO_GITHUB_REPO"))
        if not mappings or not token:
            raise ValueError("Add at least one SQL file mapping and a repository-scoped GitHub token.")
        profile["github"] = {"repository": repo, "base": base, "modules": mappings, "token_env": "STUDIO_GITHUB_TOKEN"}
        bindings.update(STUDIO_GITHUB_TOKEN=token, STUDIO_GITHUB_REPO=repo)

    return profile, bindings
