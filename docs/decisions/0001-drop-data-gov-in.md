# 0001: Drop data.gov.in (UDISE+) and use DIKSHA

**Status:** accepted · 29 Sep 2026

## Context
The first plan used data.gov.in for district-level school enrolment (UDISE+). Tests showed the API front door works but the data backend is unreliable:

- HTTP 503 on most calls, with and without a key, from a PC in India.
- HTTP 000 / 30 s timeouts from Cloud Shell, which runs in Taiwan. Non-Indian IPs appear to be dropped.
- One resource per district per year, so real coverage means thousands of calls.

## Decision
Drop data.gov.in. Use the DIKSHA content search API instead. It needs no key and returned HTTP 200 in every test.

## Consequences
- Pipeline runs on a source that is reliable enough for a daily schedule.
- Analysis uses real usage data (play sessions, ratings) instead of enrolment counts.
- Cloud Run in Mumbai calls Indian government APIs from an Indian IP.
- No district-level enrolment context. National context comes from UNESCO UIS and the World Bank.

Evidence: `docs/ingestion_design.md`, section 2.
