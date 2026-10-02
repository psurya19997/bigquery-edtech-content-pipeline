# 0002: Slice DIKSHA loads by date range

**Status:** accepted · 30 Sep 2026

## Context
DIKSHA search can only be paged up to 10,000 results. `offset = 10000` returns HTTP 500. The scoped corpus is about 430K resources, so it must be split into slices.

Splitting by board, medium or grade does not work cleanly: those fields are lists (one resource can be tagged for Classes 1–10) or are often missing, so slices would overlap or silently drop items.

## Decision
Split by date range on a single-valued, always-present field (`createdOn` for full loads, `lastUpdatedOn` for incremental). A planner bisects a range until each slice holds at most 9,000 items, leaving 1,000 headroom for items published mid-run. Pages are sorted by `identifier` so paging is stable.

Verified: `createdOn < 2019` plus `createdOn >= 2019` gives 287,708 + 629,899 = 917,607, exactly the corpus total, with no overlap and no gap.

## Consequences
- Every item is fetched once, with no board or grade double-counting.
- A one-second burst of more than 9,000 items would be flagged `over_cap` (never seen).
- Scope was cut to 10 learning categories (430K items) from 917K by excluding raw assets and certificate templates.
- Incremental runs use a watermark of the last successful run minus one day. Overlap duplicates are removed in staging.

Evidence: `docs/ingestion_design.md`, section 3; `tests/test_diksha.py`.
