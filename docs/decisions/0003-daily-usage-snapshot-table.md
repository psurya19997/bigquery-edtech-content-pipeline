# 0003: Keep usage numbers in a separate daily snapshot table

**Status:** accepted · 1 Oct 2026

## Context
DIKSHA reports only running totals (all-time play sessions, time spent, ratings). Past days cannot be fetched later, so history exists only if it is captured daily. Re-saving every full content row daily would cost far more storage than the numbers need.

## Decision
- `raw.diksha_content`: full attributes, loaded once, then only changed items each day (incremental).
- `raw.diksha_metrics_snapshot`: a narrow table of `identifier` plus the usage fields, one snapshot per day, about 430K small rows (about 20 MB per day).
- Staging derives per-day plays from day-to-day change, divided by the gap length, with decreases flagged.

## Consequences
- Storage and query cost stay small, and usage growth can be charted from day 1.
- Usage history starts on 1 Oct 2026, so trends need a few weeks of data.
- A day with two loads is deduplicated in staging (latest row per snapshot date and identifier).
- Confirmed useful: total plays rose by 40,260 between the first two snapshots.

Evidence: `docs/ingestion_design.md`, section 3.1 test 17.
