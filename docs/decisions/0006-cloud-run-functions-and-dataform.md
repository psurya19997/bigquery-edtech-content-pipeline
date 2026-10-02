# 0006: Cloud Run functions for ingestion, Dataform for transformation

**Status:** accepted · Sep–Oct 2026

## Context
The pipeline needs scheduled ingestion from four sources and a SQL transformation layer with data-quality checks, with a small budget (₹500 alert) and one person learning GCP.

## Decision
**Ingestion: one Cloud Run function per source** (Python 3.12, region `asia-south1`, entry point `run`), triggered by Cloud Scheduler.
- Isolated failures: a broken YouTube job never stops DIKSHA.
- Indian IP for Indian government APIs.
- Request timeout raised to 3,600 s (the default 300 s is too short for the DIKSHA full load).
- Max instances and concurrency of 1, so two runs can never overlap.
- Considered: Cloud Run Jobs, GitHub Actions cron, Composer (overkill for four small jobs).

**Transformation: Dataform** (GCP-native, dependency graph, assertions, scheduled workflow).
- Staging and marts are SQLX files kept in the repo.
- Assertions fail the daily run on bad keys, unmapped languages, a high sheet reject rate, or stale sources.
- Considered: dbt-core (equally valid), plain scheduled queries (no dependencies or assertions).

**Loading: batch load jobs, not streaming inserts**, because loads are free.

## Consequences
- Four deployable units, each with its own tests and log row in `ops.ingestion_log`.
- The Dataform repository has no remote Git, so releases do not auto-compile. After a SQL change: commit, then create a new compilation under Releases & scheduling. Connecting GitHub would enable automatic releases.
- Scheduler jobs use zero retries, so a failed run is logged and the next day recovers, with no accidental double loads.
