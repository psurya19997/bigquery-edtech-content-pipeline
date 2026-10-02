# 0005: How the supply–demand gap is defined

**Status:** accepted · 2 Oct 2026 (replaces a first draft)

## Context
The goal is a simple, explainable number for "this class and subject gets lots of use but has little content".

The first version took the difference of two percent-ranks (usage per resource minus resource count). Checking the Marathi top 10 against real rows showed it pushed tiny, low-demand cells to the top.

## Decision
Within each language, for one cell (board × class × subject group):

```
demand_share = cell plays      / all plays in the language
supply_share = cell resources  / all resources in the language
gap_score    = demand_share - supply_share      (percentage points)
demand_supply_ratio = demand_share / supply_share
```

A positive gap means the cell gets more of the language's plays than its share of its resources. A curriculum rule removes impossible cells (Environmental Studies only in Classes 1–5, Science and Social Science only from Class 6). Without it, multi-class tags produced fake gaps such as CBSE Class 5 Science.

## Consequences
- Gaps are comparable within a language, never across languages.
- Plays are all-time DIKSHA web-portal plays. A resource tagged for several classes counts in each.
- The dashboard hides cells with fewer than 10 resources, because tiny cells give huge ratios from a few plays.
- A rule of thumb of 10+ resources is used to call a cell "well covered". It is not an official standard.

Evidence: `dataform/definitions/marts/mart_supply_demand.sqlx`.
