# learning-content-pulse

Where are Indian-language learners underserved — high demand for school content but little supply — and what makes educational content actually get used?

An automated GCP pipeline: public education APIs (DIKSHA, YouTube RSS, UNESCO UIS, World Bank) + a Google Sheet → BigQuery (Dataform ELT) → Looker Studio.

**Status:** planning. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) and [docs/project_overview.html](docs/project_overview.html).

*Not affiliated with Khan Academy or DIKSHA. Uses public data only.*

## Quick check that the APIs are up

```bash
pip install requests
python scripts/check_apis.py
```
