"""Generate analysis/01_eda.ipynb (plain JSON, no nbformat dependency)."""

import json
from pathlib import Path

OUT = Path(r"D:\Antigravity projects\GCP Bigquery Project\analysis\01_eda.ipynb")

cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)})


def code(text):
    cells.append(
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": text.strip("\n").splitlines(True),
        }
    )


md("""
# 01 · Exploratory data analysis (descriptive)

**Question:** where is Indian-language school content scarce relative to demand, and what kind of content gets used?

Data: BigQuery project `learning-reach-473210` (asia-south1), built by the daily pipeline:
DIKSHA (public Government of India school-content platform), YouTube Data API (public stats of the
Khan Academy India channels), UNESCO UIS + World Bank indicators. All public data; not affiliated with
DIKSHA or Khan Academy.

This notebook only **describes**: what exists, how it is used, how complete each language is.
Explanations come in `02_diagnostics`, statistical tests in `03_statistics`.
""")

code("""
from google.cloud import bigquery
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT = "learning-reach-473210"
client = bigquery.Client(project=PROJECT, location="asia-south1")

def q(sql: str) -> pd.DataFrame:
    \"\"\"Run SQL in BigQuery and return a DataFrame. Tables: staging.*, marts.*\"\"\"
    df = client.query(sql).to_dataframe()
    # BigQuery INT64 arrives as pandas nullable 'Int64', which can't hold decimals:
    # describe()/median()/mean() then fail ("Invalid value ... for dtype Int64"). Use float.
    int_cols = df.select_dtypes(include=["Int64"]).columns
    return df.astype({c: "float64" for c in int_cols})

pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", "{:,.2f}".format)
plt.rcParams.update({"figure.figsize": (10, 5), "axes.spines.top": False, "axes.spines.right": False})

FOCUS = ["Hindi", "Marathi", "Gujarati", "Kannada", "Punjabi", "Assamese"]
""")

md("""
## 1. How big is the content library?
""")

code("""
overview = q(\"\"\"
SELECT
  COUNT(*) AS resources,
  COUNTIF(NOT is_teacher_content) AS student_resources,
  COUNTIF(has_usage) AS with_usage_data,
  ROUND(100 * COUNTIF(has_usage) / COUNT(*), 1) AS pct_with_usage,
  SUM(play_sessions) AS total_play_sessions,
  COUNTIF(ratings_count > 0) AS rated_resources
FROM `learning-reach-473210.marts.mart_content_features`
\"\"\")
overview.T
""")

md("""
## 2. Supply: what kind of content exists?
""")

code("""
supply = q(\"\"\"
SELECT 'language group' AS dimension, language_group AS value, COUNT(*) AS resources
FROM `learning-reach-473210.marts.mart_content_features` WHERE NOT is_teacher_content GROUP BY 1, 2
UNION ALL
SELECT 'format', format, COUNT(*) FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content GROUP BY 1, 2
UNION ALL
SELECT 'grade band', grade_band, COUNT(*) FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content GROUP BY 1, 2
UNION ALL
SELECT 'board type', board_type, COUNT(*) FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content GROUP BY 1, 2
\"\"\")

fig, axes = plt.subplots(2, 2, figsize=(14, 9))
for ax, (dim, d) in zip(axes.flat, supply.groupby("dimension")):
    d = d.sort_values("resources")
    ax.barh(d["value"], d["resources"], color="#378ADD")
    ax.set_title(f"Student resources by {dim}")
    ax.set_xlabel("resources")
plt.tight_layout()
plt.show()
""")

code("""
by_year = q(\"\"\"
SELECT EXTRACT(YEAR FROM created_on) AS year, format, COUNT(*) AS resources
FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content
GROUP BY year, format
ORDER BY year
\"\"\")
by_year.pivot(index="year", columns="format", values="resources").fillna(0).plot(
    kind="bar", stacked=True, title="New student resources per year, by format", ylabel="resources"
)
plt.show()
""")

md("""
## 3. Demand: how is content used?

DIKSHA reports **play sessions** (times a resource was opened on the web portal, all-time).
Usage of content libraries is usually extremely uneven: a few items get most plays.
""")

code("""
usage = q(\"\"\"
SELECT play_sessions, log_play_sessions, format, language_group
FROM `learning-reach-473210.marts.mart_content_features`
WHERE has_usage AND NOT is_teacher_content
\"\"\")
print(usage["play_sessions"].describe(percentiles=[.25, .5, .75, .9, .99]).to_string())

top = usage["play_sessions"].sort_values(ascending=False)
share_top1 = top.head(int(len(top) * 0.01)).sum() / top.sum()
print(f"\\nTop 1% of resources get {share_top1:.0%} of all play sessions.")

fig, axes = plt.subplots(1, 2, figsize=(14, 4))
axes[0].hist(usage["play_sessions"].clip(upper=usage["play_sessions"].quantile(.99)), bins=60, color="#888780")
axes[0].set_title("Play sessions (clipped at 99th percentile)")
axes[1].hist(usage["log_play_sessions"], bins=60, color="#378ADD")
axes[1].set_title("log(play sessions + 1): still right-skewed; regression uses robust SEs")
plt.show()
""")

code("""
by_format = (usage.groupby("format")["play_sessions"]
             .agg(resources="count", median="median", mean="mean")
             .sort_values("median", ascending=False))
by_lang = (usage.groupby("language_group")["play_sessions"]
           .agg(resources="count", median="median", mean="mean")
           .sort_values("median", ascending=False))
display(by_format)
display(by_lang)
# Medians, not means: a handful of viral items would dominate a mean.
""")

md("""
## 4. Coverage: how complete is each language?

Grid = Class 1–12 × 7 core subjects. Not applicable (curriculum, not gaps): EVS in Classes 6–12,
Science and Social Science in Classes 1–5 (taught inside EVS). A cell is **well covered** with ≥ 10 resources.
""")

code("""
cov = q(\"\"\"
SELECT language, grade_num, subject_group, n_resources
FROM `learning-reach-473210.marts.mart_language_coverage`
WHERE is_applicable AND (is_focus_language OR language = 'English')
\"\"\")

summary = (cov.assign(well=cov["n_resources"] >= 10)
              .groupby("language")["well"].mean().mul(100).round(0)
              .sort_values(ascending=False).rename("pct_cells_well_covered"))
display(summary.to_frame())

fig, axes = plt.subplots(2, 3, figsize=(16, 9), sharey=True)
for ax, lang in zip(axes.flat, FOCUS):
    grid = (cov[cov["language"] == lang]
            .pivot(index="subject_group", columns="grade_num", values="n_resources"))
    im = ax.imshow(np.log10(grid.fillna(0) + 1), cmap="Blues", aspect="auto", vmin=0, vmax=3)
    ax.set_title(lang)
    ax.set_xticks(range(grid.shape[1]), grid.columns.astype(int))
    ax.set_yticks(range(grid.shape[0]), grid.index)
    for (i, j), v in np.ndenumerate(grid.values):
        if not np.isnan(v):
            ax.text(j, i, int(v), ha="center", va="center", fontsize=7,
                    color="white" if v >= 100 else "black")
fig.colorbar(im, ax=axes, label="log10(resources + 1)", shrink=0.6)
fig.suptitle("Resources per class x subject (numbers = resources; low numbers = gaps; blank = not taught at that level)")
plt.show()
""")

md("""
## 5. Daily usage (history grows every day from 1 Oct 2026)
""")

code("""
daily = q(\"\"\"
SELECT snapshot_date, COUNT(*) AS resources, SUM(plays_per_day) AS plays_that_day
FROM `learning-reach-473210.staging.stg_diksha_metrics_daily`
GROUP BY snapshot_date
ORDER BY snapshot_date
\"\"\")
daily
""")

md("""
## 6. YouTube: Khan Academy India channels
""")

code("""
yt = q(\"\"\"
SELECT channel_title, title, published_at, is_hinglish, subject_group, view_count, duration_seconds
FROM `learning-reach-473210.marts.mart_youtube_performance`
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `learning-reach-473210.marts.mart_youtube_performance`)
\"\"\")
display(yt.groupby("channel_title")["view_count"].agg(videos="count", total_views="sum", median_views="median"))
display(yt.groupby("is_hinglish")["view_count"].agg(videos="count", median_views="median"))
display(yt.sort_values("view_count", ascending=False).head(10)[["channel_title", "title", "view_count"]])
""")

md("""
## 7. National context: India vs neighbours
""")

code("""
nat = q(\"\"\"
SELECT indicator_id, indicator_name, country_name, year, value
FROM `learning-reach-473210.marts.mart_national_context`
WHERE indicator_id IN ('CR.1', 'CR.2', 'ROFST.1.CP')
\"\"\")
fig, axes = plt.subplots(1, 3, figsize=(16, 4))
for ax, (ind, d) in zip(axes, nat.groupby("indicator_id")):
    for country, c in d.groupby("country_name"):
        c = c.sort_values("year")
        ax.plot(c["year"], c["value"], marker="o", label=country,
                linewidth=3 if country == "India" else 1)
    ax.set_title(d["indicator_name"].iloc[0][:55])
axes[0].legend()
plt.tight_layout()
plt.show()
""")

md("""
## Summary (fill in after running)

- **Library:** … resources, …% have usage data.
- **Usage is concentrated:** top 1% of resources get …% of plays.
- **Formats:** median plays highest for …
- **Coverage:** English/Hindi complete; Punjabi and Assamese least complete of the focus languages.
  Biggest gaps: Assamese English (all classes) and Hindi (none); Punjabi primary (Classes 1–5);
  Marathi/Kannada Classes 11–12; Gujarati Classes 1–2.
- **Context:** India's primary completion 94.2% (2019), behind Sri Lanka (99.6%, 2023).

Next: `02_diagnostics` (why) and `03_statistics` (regression + hypothesis tests).
""")

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", OUT, len(cells), "cells")
