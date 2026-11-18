# SQL Server Stored Procedure Code Review Agent

Review a T-SQL stored procedure or view using static rules, SQL Server catalog and runtime evidence, and optional AI-generated T-SQL rewrites. The collector runs SELECT statements against metadata and DMVs. The reviewed module and proposed rewrites stay under the caller's control.

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

## Choose the AI backend

Use `--ai-provider azure`, `--ai-provider openai`, or `--ai-provider compatible` to select a provider for one run. For a server-wide default, set `SQL_REVIEW_AI_PROVIDER` and invoke the CLI with `--ai`. Without a configured default, `--ai` selects Azure OpenAI.

| Provider | Configuration |
| --- | --- |
| `azure` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`, and optionally `AZURE_OPENAI_API_VERSION` |
| `openai` | `OPENAI_API_KEY` and `OPENAI_MODEL` |
| `compatible` | `SQL_REVIEW_AI_BASE_URL`, `SQL_REVIEW_AI_MODEL`, and `SQL_REVIEW_AI_API_KEY` if the endpoint requires authentication |

For example, after configuring an OpenAI-compatible endpoint such as local Ollama:

```sh
python reviewer.py --procedure dbo.usp_GetDeals --ai-provider compatible --out ai-review.md
```

Use a provider and model that support Chat Completions. An explicit AI flag sends the complete reviewed definition, findings, and collected evidence to the selected endpoint. That can include SQL identifiers, index filter predicates, wait resources, and derived plan findings. The raw Showplan stays local. Use a provider approved for those data. Configuring a default alone keeps AI disabled. A provider failure leaves a local report, exits with status 1, and does not retry with a different provider. Review and validate generated SQL before using it.

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

- `reviewer.py` — CLI, deterministic rules, catalog collection and optional AI provider selection.
- `sql/diagnostics.sql` — named, parameterized SELECT queries loaded by the CLI.
- `sql/safe_example_rewrite.sql` — illustrative paginated stored procedure.
- `tests/test_reviewer.py` — offline rule and report tests.

## Browser interface

From the project directory, start the local web interface:

```sh
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8000`. Paste T-SQL or load the built-in example for an offline review. Choose **Database object** to review a stored procedure or view from the SQL Server configured in `SQLSERVER_CONNECTION_STRING`. You can attach a Showplan file, supply a live session ID in database mode, and download the findings as Markdown.

AI is off by default in the browser. Select a provider to request a proposed rewrite. Provider credentials and models come from the same server-side settings as the CLI. The interface is designed for single-user loopback access.

### Review a live rewrite in GitHub

Set `SQLSERVER_CONNECTION_STRING` on the machine running Studio to the intended SQL Server database. Use a read-only diagnostic identity and a private network route where required. In **Database object**, select a stored procedure or view and choose an AI provider. The app reads the live definition, catalog metadata, Query Store history when available, and waits if you supply an active session ID.

Set `SQL_REVIEW_GITHUB_REPOSITORY`, `SQL_REVIEW_GITHUB_BASE` and `SQL_REVIEW_GITHUB_MODULES` using the sample in `.env.example`. Store `SQL_REVIEW_GITHUB_TOKEN` as a server-side secret scoped to the repository, with Contents and Pull requests write access. Use the repository that owns the deployment SQL. Each mapping must point to an existing UTF-8 .sql file on the base branch whose content matches the live definition, ignoring BOM, line endings, and surrounding whitespace. Reconcile differences before using the draft workflow.

Compare the original and candidate, edit the candidate, then choose **Create draft PR**. Studio writes the candidate to a new branch and opens a draft with finding titles and a staging checklist. Configure branch protection and production deployment approvals separately. Studio does not merge or deploy the candidate. This browser app remains a single-user loopback tool.

### Configure separate tenant installations

Use the same application source for each installation. Copy `deployment.example.json` to a tenant-specific configuration file and point `SQL_REVIEW_PROFILE` to that file before starting the browser app. The app reads the profile at startup. Restart it to apply configuration changes. Keep the JSON free of secrets. It names the environment variables the app uses for SQL connections, model credentials, and GitHub tokens.

- `sql_connection_env` points to that tenant's read-only ODBC connection string. One SQL connection is configured per installation.
- `ai` defines the approved choices shown in the browser. Each points to a provider, model or deployment, and any required endpoint and credential variables. Use `azure` for Azure OpenAI deployments with the supported Chat Completions API, `openai` for the hosted OpenAI API, and `compatible` for compatible Chat Completions endpoints. Use an empty mapping to disable AI. AI remains opt-in.
- `github` sets the SQL source repository, base branch, module-to-file mapping, and token environment variable. Omit it to disable draft PR creation in that installation.

Each tenant should have its own app instance and access boundary, SQL diagnostic identity, and secret bindings. In Azure, bind secret values through the hosting service's Key Vault integration. The current browser app uses loopback access. Entra ID, Azure hosting and networking, and production deployment approvals must be configured separately before multi-user access. When no profile is set, the browser uses the original environment variables. The CLI continues using its existing environment variables and flags.

### Configure a local session in the browser

From **Deployment setup**, open **Settings**. Enter a read-only ODBC connection string, up to three approved model choices, and optional GitHub draft PR settings. Choose **Save settings** and return to the review screen. The values take effect immediately for the running Studio process, stay in memory, and clear on restart. Clear browser-entered settings to return to the original environment defaults. Use the server-managed deployment profile above for durable tenant settings. Studio makes a configured tenant profile read-only in the browser.
