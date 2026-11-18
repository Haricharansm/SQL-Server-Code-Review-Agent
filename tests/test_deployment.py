import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app import app
from deployment import load_profile
from github_review import review_target
from reviewer import ai_review


class DeploymentTests(TestCase):
    def setUp(self):
        self.profile = load_profile(Path(__file__).resolve().parents[1] / "deployment.example.json")

    def test_profile_limits_visible_models_and_routes_live_review(self):
        profile = self.profile
        with patch.dict(app.config, {"DEPLOYMENT_PROFILE": profile}), patch.dict(
            "os.environ", {"TENANT_SQL_CONNECTION": "private-connection"}
        ), patch("app.collect", return_value=("SELECT 1", {}, [])) as collect, patch(
            "app.ai_review", return_value="```sql\nSELECT 2;\n```"
        ) as rewrite:
            client = app.test_client()
            with client.session_transaction() as session:
                session["csrf"] = "test"
            response = client.post("/", data=dict(
                csrf="test", source="database", module="dbo.usp_GetDeals", provider="deep-review"
            ))
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Fast review", response.data)
            self.assertIn(b"example-org/database-source/procedures/usp_GetDeals.sql", response.data)
            self.assertNotIn(b"private-connection", response.data)
            collect.assert_called_once_with("private-connection", "dbo", "usp_GetDeals", None)
            self.assertEqual(rewrite.call_args.kwargs["config"], profile["ai"]["deep-review"])
            invalid = client.post("/", data=dict(csrf="test", source="editor", sql="SELECT 1", provider="openai"))
            self.assertEqual(invalid.status_code, 400)
            rewrite.assert_called_once()

    def test_model_config_uses_its_own_environment_variables(self):
        with patch.dict("os.environ", {
            "TENANT_FOUNDRY_KEY": "tenant-key", "TENANT_FOUNDRY_ENDPOINT": "https://example.invalid",
            "TENANT_DEEP_DEPLOYMENT": "deployment-two",
        }), patch("openai.AzureOpenAI") as client:
            complete = client.return_value.chat.completions.create
            complete.return_value.choices[0].message.content = "review"
            self.assertEqual(ai_review("SELECT 1", [], {}, "azure",
                                       config=self.profile["ai"]["deep-review"]), "review")
            self.assertEqual(complete.call_args.kwargs["model"], "deployment-two")
            self.assertEqual(client.call_args.kwargs["api_key"], "tenant-key")

    def test_profile_mapping_takes_precedence_over_legacy_repository(self):
        with patch.dict("os.environ", {"SQL_REVIEW_GITHUB_REPOSITORY": "other/repo"}):
            self.assertEqual(review_target("dbo.usp_GetDeals", self.profile["github"]),
                             ("example-org/database-source", "main", "procedures/usp_GetDeals.sql"))

    def test_invalid_secret_reference_is_rejected(self):
        self.profile["ai"]["fast-review"]["key_env"] = "looks-like-a-secret"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(self.profile))
            with self.assertRaisesRegex(ValueError, "environment variable references"):
                load_profile(path)

    def test_homepage_shows_setup_status_without_secret_values(self):
        with patch.dict(app.config, {"DEPLOYMENT_PROFILE": self.profile}), patch.dict(
            "os.environ", {"TENANT_SQL_CONNECTION": "private-sql-value",
                          "TENANT_FOUNDRY_KEY": "private-ai-value",
                          "TENANT_FOUNDRY_ENDPOINT": "https://example.invalid",
                          "TENANT_FAST_DEPLOYMENT": "fast",
                          "TENANT_GITHUB_TOKEN": "private-github-value"}, clear=True
        ):
            response = app.test_client().get("/")
        page = response.get_data(as_text=True)
        for expected in ("Deployment setup", "Example tenant", "Connection configured",
                         "1 of 2 configured", "Repository configured", "Deep review · setup needed"):
            self.assertIn(expected, page)
        for secret in ("private-sql-value", "private-ai-value", "private-github-value"):
            self.assertNotIn(secret, page)

    def test_browser_settings_are_used_without_echoing_credentials(self):
        from re import search

        with patch.dict(app.config, {"DEPLOYMENT_PROFILE": None, "LOCAL_SETTINGS": (None, {}, "")}):
            client = app.test_client()
            self.assertIn(b'href="/settings"', client.get("/").data)
            page = client.get("/settings")
            csrf = search(r'name="csrf" value="([^"]+)"', page.get_data(as_text=True))[1]
            response = client.post("/settings", data=dict(
                csrf=csrf, name="Test tenant", connection="secret-odbc",
                provider_1="azure", label_1="Foundry fast", model_1="fast-deployment",
                endpoint_1="https://example.invalid", key_1="secret-ai",
                repository="example/sql", base="main", modules="dbo.p=sql/p.sql", github_token="secret-git",
            ), follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Settings updated", response.data)
            self.assertNotIn(b"secret-", response.data)
            home = client.get("/")
            for expected in (b"Connection configured", b"1 of 1 configured", b"Repository configured", b"Foundry fast"):
                self.assertIn(expected, home.data)
            with patch("app.collect", return_value=("SELECT 1", {}, [])) as collect, patch(
                "openai.AzureOpenAI"
            ) as model:
                model.return_value.chat.completions.create.return_value.choices[0].message.content = (
                    "```sql\nSELECT 2;\n```"
                )
                result = client.post("/", data=dict(csrf=csrf, source="database", module="dbo.p", provider="model-1"))
                collect.assert_called_once_with("secret-odbc", "dbo", "p", None)
                self.assertEqual(model.call_args.kwargs["api_key"], "secret-ai")
                self.assertIn(b"Create draft PR", result.data)
            client.post("/settings", data=dict(csrf=csrf, action="reset"))
            self.assertNotIn(b"Foundry fast", client.get("/").data)

    def test_managed_settings_reject_browser_edits(self):
        with patch.dict(app.config, {"DEPLOYMENT_PROFILE": self.profile}):
            client = app.test_client()
            self.assertIn(b"Managed deployment", client.get("/settings").data)
            with client.session_transaction() as session:
                csrf = session["csrf"]
            self.assertEqual(client.post("/settings", data=dict(csrf=csrf, name="Override")).status_code, 403)
