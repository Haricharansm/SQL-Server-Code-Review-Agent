import base64
from unittest import TestCase
from unittest.mock import patch

import httpx

from github_review import DraftError, create_draft, source_digest


class DraftTests(TestCase):
    def setUp(self):
        config = patch.dict("os.environ", {
            "SQL_REVIEW_GITHUB_REPOSITORY": "team/database",
            "SQL_REVIEW_GITHUB_MODULES": '{"dbo.p":"procedures/p.sql"}',
            "SQL_REVIEW_GITHUB_TOKEN": "fake",
            "SQL_REVIEW_GITHUB_BASE": "main",
        })
        config.start()
        self.addCleanup(config.stop)

    def simulate(self, stored):
        import json

        calls = []
        responses = [
            {"object": {"sha": "base-sha"}},
            {"type": "file", "encoding": "base64", "content": base64.b64encode(stored.encode()).decode(),
             "sha": "blob-sha"},
            {}, {}, {"html_url": "https://github.com/team/database/pull/42"},
        ]

        def request(req):
            calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
            return httpx.Response(200, json=responses[len(calls) - 1])

        client = httpx.Client
        factory = patch("github_review.httpx.Client",
                        side_effect=lambda **options: client(transport=httpx.MockTransport(request), **options))
        return calls, factory

    def test_draft_creates_branch_and_changes_only_mapped_file(self):
        calls, factory = self.simulate("SELECT 1")
        with factory:
            result = create_draft("dbo.p", source_digest("SELECT 1"), "SELECT 2", ["One finding"])
        self.assertEqual(result, "https://github.com/team/database/pull/42")
        self.assertEqual([call[0] for call in calls], ["GET", "GET", "POST", "PUT", "POST"])
        self.assertEqual(calls[3][1], "/repos/team/database/contents/procedures/p.sql")
        self.assertEqual(base64.b64decode(calls[3][2]["content"]), b"SELECT 2")
        self.assertTrue(calls[4][2]["draft"])
        self.assertEqual(calls[4][2]["head"], calls[3][2]["branch"])

    def test_baseline_drift_blocks_all_github_writes(self):
        calls, factory = self.simulate("SELECT 3")
        with factory, self.assertRaisesRegex(DraftError, "differs"):
            create_draft("dbo.p", source_digest("SELECT 1"), "SELECT 2", [])
        self.assertEqual([call[0] for call in calls], ["GET", "GET"])
