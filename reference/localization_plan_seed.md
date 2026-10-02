# Localization Plan Seed Data

This dataset contains synthetic seed data for the "Localization Plan" sheet, representing an EdTech nonprofit's content planning pipeline. It simulates hand-entered data with realistic data-quality issues, which will be loaded into BigQuery and used to test the cleaning layer and data-quality checks. All data in this file is completely synthetic and does not represent real people or organizations.

## Columns

| Name | Meaning | Allowed Clean Values |
|---|---|---|
| `plan_id` | Unique identifier for each content plan | LP-001 to LP-024 |
| `language` | Target language for the content | Hindi, Marathi, Gujarati, Kannada, Punjabi, Assamese, Odia, English |
| `board` | Educational board | CBSE, State (Maharashtra), State (Gujarat), State (Karnataka), State (Punjab), State (Assam), State (Uttar Pradesh), State (Odisha) |
| `grade` | Target class level | Class 3 to Class 10 |
| `subject` | Subject being taught | Mathematics, Science, English, Social Science, Environmental Studies, Hindi |
| `resources_planned` | Number of resources to create | Whole number between 10 and 120 |
| `owner` | Team responsible for creation | content-north, content-west, content-south, content-east, translation-team |
| `status` | Current status of the plan | planned, in_production, published, dropped |
| `target_date` | Expected completion date | YYYY-MM-DD between 2026-10-01 and 2027-06-30 |
| `last_updated` | Date the record was last modified | YYYY-MM-DD between 2026-08-01 and 2026-09-30, not after target_date |
| `notes` | Additional comments | Short free text or empty |

## Planted Problems

| Row | Plan ID | Column | Messy Value | Expected Cleaned Value / Action |
|---|---|---|---|---|
| 3 | LP-003 | language | `"Marathi "` | Marathi |
| 9 | LP-009 | language | `marathi` | Marathi |
| 10 | LP-010 | language | `MR` | Marathi |
| 11 | LP-011 | language | `Oriya` | Odia |
| 12 | LP-012 | grade | `6` | Class 6 |
| 13 | LP-013 | grade | `VII` | Class 7 |
| 14 | LP-014 | target_date | `15/12/2026` | 2026-12-15 |
| 15 | LP-015 | status | *(empty)* | reject |
| 16 | LP-016 | resources_planned | `forty` | reject |
| 25 | LP-005 | *all* | *(duplicate row)* | keep one copy |
