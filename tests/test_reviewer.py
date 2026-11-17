from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys
from unittest.mock import patch

from reviewer import (NAMESPACE, ai_review, catalog_findings, load_queries, main, plan_findings,
                      render, request_findings, static_findings)


class ReviewerTests(unittest.TestCase):
    def test_static_candidates(self):
        findings = static_findings(
            "SELECT DISTINCT d.* FROM dbo.Deal d WHERE YEAR(d.CreatedAt) = 2025; "
            "SELECT COUNT(*) FROM dbo.Deal;"
        )
        self.assertEqual({f.title for f in findings}, {
            "Wildcard projection", "Deduplication candidate", "Function in predicate candidate"
        })

    def test_literals_comments_and_count_are_not_findings(self):
        self.assertEqual(static_findings(
            "/* SELECT DISTINCT * */ SELECT 'OPENQUERY(x)', COUNT(*) FROM dbo.Deal; -- CURSOR"
        ), [])

    def test_live_waits_are_distinct_observations(self):
        sample = {"session_id": 71, "request_id": 0, "wait_ms": 250, "blocking_session_id": 64}
        findings = request_findings([dict(sample, wait_type=wait) for wait in
                                     ("LCK_M_U", "ASYNC_NETWORK_IO", "RESOURCE_SEMAPHORE", None)])
        self.assertEqual(len(findings), 3)
        self.assertEqual({f.basis for f in findings}, {"Live snapshot"})
        self.assertIn("blocker 64", findings[0].evidence)

    def test_plan_runtime_and_spill(self):
        xml = f"""<ShowPlanXML xmlns="{NAMESPACE['p']}"><RelOp NodeId="0" PhysicalOp="Sort">
          <Warnings><SpillToTempDb SpillLevel="1"/></Warnings>
          <Sort><RelOp NodeId="1" PhysicalOp="Nested Loops"><NestedLoops>
            <RelOp NodeId="2" PhysicalOp="Index Seek" EstimateRows="1">
              <RunTimeInformation><RunTimeCountersPerThread ActualRows="45"/></RunTimeInformation>
            </RelOp>
            <RelOp NodeId="3" PhysicalOp="Table Scan">
              <RunTimeInformation><RunTimeCountersPerThread ActualExecutions="45"/></RunTimeInformation>
            </RelOp>
          </NestedLoops></RelOp></Sort>
        </RelOp></ShowPlanXML>"""
        with TemporaryDirectory() as folder:
            plan = Path(folder) / "actual.sqlplan"
            plan.write_text(xml)
            findings = plan_findings(plan)
        self.assertEqual({f.basis for f in findings}, {"Plan warning", "Plan runtime counters"})
        self.assertIn("45", findings[1].evidence)

    def test_estimated_plan_has_no_observed_counters(self):
        with TemporaryDirectory() as folder:
            plan = Path(folder) / "estimated.sqlplan"
            plan.write_text(f'<ShowPlanXML xmlns="{NAMESPACE["p"]}"><RelOp PhysicalOp="Remote Query"/></ShowPlanXML>')
            findings = plan_findings(plan)
        self.assertEqual({f.basis for f in findings}, {"Plan operator", "Plan metadata"})

    def test_remote_synonym_and_wide_columns_in_report(self):
        dependencies = [dict(server_name=None, database_name=None, schema_name="dbo",
                             object_name="Deal", synonym_server="salesforce",
                             synonym_target="[salesforce].[crm].[sales].[Deal]", external_table=False)]
        objects = {"1": {"name": "dbo.Account", "wide_columns": [{"column_name": "Description"}]}}
        findings = catalog_findings(dependencies, objects)
        report = render("dbo.GetDeals", findings, {}, [])
        for expected in ("Remote or external dependency", "dbo.Account exposes Description", "Validation"):
            self.assertIn(expected, report)

    def test_azure_request_uses_configured_deployment(self):
        config = {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "placeholder",
            "AZURE_OPENAI_DEPLOYMENT": "review-deployment"
        }
        with patch.dict("os.environ", config), patch("openai.AzureOpenAI") as factory:
            complete = factory.return_value.chat.completions.create
            complete.return_value.choices[0].message.content = "Proposed review"
            self.assertEqual(ai_review("SELECT 1", [], {}), "Proposed review")
            self.assertEqual(complete.call_args.kwargs["model"], "review-deployment")
            self.assertIn("SELECT 1", complete.call_args.kwargs["messages"][1]["content"])

    def test_openai_and_compatible_use_their_own_settings(self):
        config = {
            "OPENAI_API_KEY": "hosted-key",
            "OPENAI_MODEL": "hosted-model",
            "SQL_REVIEW_AI_BASE_URL": "http://localhost:11434/v1",
            "SQL_REVIEW_AI_MODEL": "local-model",
            "SQL_REVIEW_AI_API_KEY": "endpoint-key",
        }
        with patch.dict("os.environ", config), patch("openai.OpenAI") as factory:
            complete = factory.return_value.chat.completions.create
            complete.return_value.choices[0].message.content = "Rewrite"
            for provider, model, endpoint, key in (
                ("openai", "hosted-model", "https://api.openai.com/v1", "hosted-key"),
                ("compatible", "local-model", "http://localhost:11434/v1", "endpoint-key"),
            ):
                with self.subTest(provider=provider):
                    self.assertEqual(ai_review("SELECT 1", [], {}, provider), "Rewrite")
                    factory.assert_called_with(base_url=endpoint, api_key=key, timeout=60)
                    self.assertEqual(complete.call_args.kwargs["model"], model)

    def test_cli_provider_override_default_and_opt_in(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "query.sql"
            source.write_text("SELECT 1")
            report = Path(folder) / "review.md"
            argv = ["reviewer.py", "--sql-file", str(source), "--out", str(report)]
            with patch.dict("os.environ", {"SQL_REVIEW_AI_PROVIDER": "compatible"}), patch(
                "reviewer.ai_review", return_value="Generated SQL"
            ) as review:
                with patch.object(sys, "argv", argv):
                    self.assertEqual(main(), 0)
                    review.assert_not_called()
                for flags, provider in ((["--ai"], "compatible"),
                                        (["--ai-provider", "openai"], "openai")):
                    with patch.object(sys, "argv", argv + flags):
                        self.assertEqual(main(), 0)
                        review.assert_called_with("SELECT 1", [], {}, provider)
                        self.assertIn(f"AI provider used for proposed T-SQL: {provider}", report.read_text())

    def test_missing_provider_configuration_leaves_local_report(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "query.sql"
            source.write_text("SELECT DISTINCT * FROM dbo.Deal")
            report = Path(folder) / "review.md"
            argv = ["reviewer.py", "--sql-file", str(source), "--out", str(report),
                    "--ai-provider", "openai"]
            with patch.dict("os.environ", {"OPENAI_MODEL": ""}), patch.object(sys, "argv", argv):
                self.assertEqual(main(), 1)
            text = report.read_text()
            self.assertIn("Deduplication candidate", text)
            self.assertIn("Set OPENAI_MODEL", text)

    def test_query_bundle_parameter_counts(self):
        self.assertEqual({name: sql.count("?") for name, sql in load_queries().items()},
                         dict(object=2, dependencies=1, columns=1, indexes=1, statistics=1, request=1, history=1))


if __name__ == "__main__":
    unittest.main()
