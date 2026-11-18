from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from app import app


class StudioTests(TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def review(self, **fields):
        data = dict(csrf=self.csrf, source="editor", sql="SELECT DISTINCT * FROM dbo.Deal")
        data.update(fields)
        return self.client.post("/", data=data)

    def test_editor_review_and_report(self):
        with patch("app.ai_review") as generate:
            body = self.review().get_data(as_text=True)
        generate.assert_not_called()
        self.assertIn("Wildcard projection", body)
        self.assertIn("Deduplication candidate", body)
        self.assertIn("Download report", body)

    def test_ai_output_is_escaped_and_candidate_is_available_to_copy(self):
        proposal = "```sql\nSELECT '<script>untrusted</script>';\n```"
        with patch("app.ai_review", return_value=proposal) as generate:
            body = self.review(provider="azure").get_data(as_text=True)
        self.assertEqual(generate.call_args.args[-1], "azure")
        self.assertIn("Copy SQL", body)
        self.assertIn("&lt;script&gt;untrusted", body)
        self.assertNotIn("<script>untrusted", body)

    def test_upload_validation_preserves_editor(self):
        response = self.review(plan=(BytesIO(b"<bad>"), "invalid.sqlplan"))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"not valid SQL Server Showplan XML", response.data)
        self.assertIn(b"SELECT DISTINCT * FROM dbo.Deal", response.data)

    def test_database_review_collects_only_from_configured_connection(self):
        evidence = dict(dependencies=[], objects={}, live_request=[])
        with patch.dict("os.environ", {"SQLSERVER_CONNECTION_STRING": "test connection"}), patch(
                "app.collect", return_value=("SELECT DISTINCT * FROM dbo.Deal", evidence, [])) as collect:
            response = self.review(source="database", module="dbo.usp_GetDeals", spid="57")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Deduplication candidate", response.data)
        collect.assert_called_once_with("test connection", "dbo", "usp_GetDeals", 57)

    def test_token_and_untrusted_hosts_are_rejected(self):
        self.assertEqual(self.review(csrf="invalid").status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "example.com"}).status_code, 400)

    def test_live_rewrite_remains_editable_until_explicit_draft_request(self):
        import re

        evidence = dict(dependencies=[], objects={}, live_request=[])
        config = {"SQLSERVER_CONNECTION_STRING": "test", "SQL_REVIEW_GITHUB_REPOSITORY": "team/db",
                  "SQL_REVIEW_GITHUB_MODULES": '{"dbo.p":"sql/p.sql"}'}
        with patch.dict("os.environ", config), patch(
            "app.collect", return_value=("SELECT 1", evidence, [])
        ), patch("app.ai_review", return_value="```sql\nSELECT 2;\n```"), patch(
            "app.create_draft", return_value="https://github.com/team/db/pull/1"
        ) as create:
            response = self.review(source="database", module="dbo.p", provider="azure")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Original T-SQL", response.data)
            create.assert_not_called()
            token = re.search(r'name="draft_token" value="([^"]+)"', response.get_data(as_text=True))[1]
            forged = self.client.post("/draft", data=dict(csrf=self.csrf, candidate="SELECT 3;", draft_token="bad"))
            self.assertEqual(forged.status_code, 400)
            create.assert_not_called()
            draft = self.client.post("/draft", data=dict(csrf=self.csrf, candidate="SELECT 3;", draft_token=token))
            self.assertEqual(draft.json["url"], "https://github.com/team/db/pull/1")
            self.assertEqual(create.call_args.args[2], "SELECT 3;\n")

    def test_compare_returns_edited_line_changes(self):
        response = self.client.post("/compare", data=dict(
            csrf=self.csrf, original="SELECT 1;", candidate="SELECT 3;"
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn("-SELECT 1;\n+SELECT 3;", response.json["diff"])
