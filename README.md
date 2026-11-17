# SQL Server Stored Procedure Code Review Agent

Review a T-SQL stored procedure or view using static rules, SQL Server catalog and runtime evidence, and optional Azure OpenAI recommendations. The collector runs SELECT statements against metadata and DMVs. The reviewed module and proposed rewrites stay under the caller's control.

## Setup

Use Python 3.10 or later. Install Microsoft ODBC Driver 18 and the system ODBC manager for connected reviews. Target SQL Server 2016 SP1 or later. Install dependencies in a virtual environment.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Set `SQLSERVER_CONNECTION_STRING` in your shell or secret manager using the format in `.env.example`. Use a least-privilege account with access to the database and module definition. Depending on the version and query, catalog or DMV visibility can require database-level `VIEW DEFINITION`, `VIEW DATABASE STATE`, `VIEW SERVER STATE` (through SQL Server 2019), or `VIEW SERVER PERFORMANCE STATE` (2022 and later). Ask the DBA to scope the access to the review job.

## Usage

```sh
python reviewer.py --sql-file sql/safe_example_rewrite.sql --out example-review.md
python reviewer.py --procedure dbo.usp_GetDeals --out review.md
python reviewer.py --object dbo.vDeals --plan actual.sqlplan --out plan-review.md
python reviewer.py --procedure dbo.usp_GetDeals --session-id 57 --out live-review.md
python reviewer.py --procedure dbo.usp_GetDeals --ai --out ai-review.md
python -m unittest discover -s tests -v
```

Capture the actual plan while running representative parameter values in an appropriate test environment. Run live sampling from a separate connection while the target SPID is active. The SPID is supplied by the operator. Verify it is executing the intended statement, and repeat the sample to catch intermittent waits. Query Store collects historical aggregates when enabled and populated. The report displays up to 20 recent rows, with durations and CPU in microseconds and reads in logical pages.

Set the Azure variables from `.env.example` to enable AI. The deployment must support Azure OpenAI chat completions. The explicit `--ai` flag sends the complete reviewed definition, findings, and collected evidence to the configured Azure deployment. The evidence can include SQL identifiers, index filter predicates and wait resources. Derived plan findings are sent without uploading the raw Showplan. Use a deployment approved for those data. An AI error leaves a local report and exits with status 1.

## Interpreting results

- Wildcard, wide-column, cursor, predicate and DISTINCT rules are triage heuristics. The SQL tokenizer can miss constructs such as wildcard projections following TOP or appearing later in the select list. Procedure dependency columns describe available columns. Verify its result metadata and actual returned bytes separately.
- A repeated inner scan is a lead. Check the actual plan for the responsible operator, predicate, rows and reads. Review index leading keys, filtered indexes and statistics affected by the nightly reload. A read count or a stale timestamp alone does not establish root cause.
- Compare a spill warning with the operator's input width, cardinality and memory grant. Removing DISTINCT requires proof that the new query preserves multiplicity.
- A fast COUNT(*) may use a different plan. Repeated ASYNC_NETWORK_IO shows the server waiting on client consumption. Use the same full result with and without grid rendering to isolate the display component.
- Resolve the blocking transaction and correlate it with the load before attributing a lock wait. Live data shows a point in time.
- Catalog traversal follows local views up to eight levels. Inspect synonym targets, called procedures, dynamic SQL and unresolved cross-database references separately. A declared remote dependency can be unused by a particular execution.

## Validating a proposed change

Run baseline and candidate with the same parameter sets and comparable cache and load conditions in a test database. Compare result columns and types, values, NULL handling, row multiplicity and any order guaranteed by the contract. For procedures with writes, also compare side effects, transactions and error behavior. Capture actual plans and compare logical reads, CPU, elapsed time, spills and row estimates. Check client result drain time separately when output is wide.

The example procedure illustrates an opt-in narrow result and keyset pagination. It assumes a positive unique deal key and a unique Account ID, and deliberately defines a new caller contract. Adapt the table and column names, join kind, types and filters to your own schema before use.

## Files

- `reviewer.py` — CLI, deterministic rules, catalog collection and optional Azure integration.
- `sql/diagnostics.sql` — named, parameterized SELECT queries loaded by the CLI.
- `sql/safe_example_rewrite.sql` — illustrative paginated stored procedure.
- `tests/test_reviewer.py` — offline rule and report tests.
