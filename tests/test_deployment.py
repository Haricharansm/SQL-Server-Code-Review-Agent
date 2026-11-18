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
