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
