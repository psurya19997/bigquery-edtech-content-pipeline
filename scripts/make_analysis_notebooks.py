"""Generate analysis/02_diagnostics.ipynb and analysis/03_statistics.ipynb.

Plain JSON (no nbformat dependency). Run:  python scripts/make_analysis_notebooks.py
Upload the .ipynb files to BigQuery Studio (Notebook > Upload) and Run all.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SETUP = '''
from google.cloud import bigquery
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT = "learning-reach-473210"
client = bigquery.Client(project=PROJECT, location="asia-south1")

def q(sql: str) -> pd.DataFrame:
    """Run SQL in BigQuery and return a DataFrame (INT64 -> float, see 01_eda)."""
    df = client.query(sql).to_dataframe()
    int_cols = df.select_dtypes(include=["Int64"]).columns
    return df.astype({c: "float64" for c in int_cols})

pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", "{:,.3f}".format)
plt.rcParams.update({"figure.figsize": (10, 5), "axes.spines.top": False, "axes.spines.right": False})
FOCUS = ["Hindi", "Marathi", "Gujarati", "Kannada", "Punjabi", "Assamese"]
'''


class Notebook:
    def __init__(self):
        self.cells = []

    def md(self, text):
        self.cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)})

    def code(self, text):
        self.cells.append({"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                           "source": text.strip("\n").splitlines(True)})

    def save(self, path):
        nb = {"cells": self.cells,
              "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                           "language_info": {"name": "python"}},
              "nbformat": 4, "nbformat_minor": 5}
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", path, len(self.cells), "cells")


# ===================================================================== 02 diagnostics
d = Notebook()
d.md("""
# 02 · Diagnostics: why?

`01_eda` described what exists. This notebook digs into **why**: where the gaps are and how big,
whether the team's plan targets them, why some content has no usage data, how teacher content
distorts averages, and how clean DIKSHA's language labels are.
""")
d.code(SETUP)

d.md("""
## 1. Biggest gaps per focus language

Two views (`marts.mart_supply_demand`, within each language):
- **gap_score** = demand share − supply share → big absolute gaps (many plays, relatively few items)
- **demand_supply_ratio** → intense small gaps (e.g. 5 items with 65K plays each); cells with ≥ 10 resources
""")
d.code('''
gaps = q("""
SELECT language, board, grade_label, subject_group, n_resources,
       sessions_per_resource, demand_share, supply_share, gap_score, demand_supply_ratio
FROM `learning-reach-473210.marts.mart_supply_demand`
WHERE is_focus_language
""")
for lang in FOCUS:
    g = gaps[gaps["language"] == lang]
    top_gap = g.nlargest(5, "gap_score")
    top_ratio = g[g["n_resources"] >= 10].nlargest(5, "demand_supply_ratio")
    print(f"\\n=== {lang} ===")
    display(top_gap[["board", "grade_label", "subject_group", "n_resources", "gap_score"]]
            .assign(gap_points=lambda x: 100 * x["gap_score"]).drop(columns="gap_score"))
    display(top_ratio[["board", "grade_label", "subject_group", "n_resources", "demand_supply_ratio"]])
''')

d.md("""
## 2. Is the plan aimed at the gaps? (Localization Plan sheet is SYNTHETIC)
""")
d.code('''
plan = q("""
SELECT alignment, COUNT(*) AS plans, SUM(resources_planned) AS resources_planned
FROM `learning-reach-473210.marts.mart_plan_vs_gap` GROUP BY alignment ORDER BY plans DESC
""")
plan["share_of_effort"] = plan["resources_planned"] / plan["resources_planned"].sum()
display(plan)

unplanned = q("""
SELECT language, gap_rank, board, grade_label, subject_group, n_resources, gap_score
FROM `learning-reach-473210.marts.mart_unplanned_gaps`
WHERE gap_rank <= 3 ORDER BY language, gap_rank
""")
display(unplanned.assign(gap_points=lambda x: 100 * x["gap_score"]).drop(columns="gap_score"))
''')

d.md("""
## 3. Why do some resources have no usage data?

~18% of student resources carry no play count. If that is concentrated in some formats or boards,
usage-based comparisons are biased (selection bias) — important caveat for `03_statistics`.
""")
d.code('''
cov = q("""
SELECT format, board_type, language_group, COUNT(*) AS resources,
       AVG(IF(has_usage, 1, 0)) AS pct_with_usage
FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content
GROUP BY CUBE(format, board_type, language_group)
""")
for dim in ["format", "board_type", "language_group"]:
    others = [c for c in ["format", "board_type", "language_group"] if c != dim]
    t = cov[cov[dim].notna() & cov[others].isna().all(axis=1)]
    display(t[[dim, "resources", "pct_with_usage"]].sort_values("pct_with_usage"))
''')

d.md("""
## 4. Teacher content: does it distort the averages?
""")
d.code('''
teach = q("""
SELECT is_teacher_content, COUNT(*) AS resources,
       APPROX_QUANTILES(play_sessions, 2)[OFFSET(1)] AS median_plays,
       AVG(play_sessions) AS mean_plays, SUM(play_sessions) AS total_plays
FROM `learning-reach-473210.marts.mart_content_features`
WHERE has_usage GROUP BY is_teacher_content
""")
teach["share_of_all_plays"] = teach["total_plays"] / teach["total_plays"].sum()
teach
''')

d.md("""
## 5. Label quality: does the title's script match the medium?

A resource tagged `medium = Marathi` should usually have a title in Devanagari (or Latin, which is
common for English titles). A title in **another Indian script** (e.g. Gurmukhi for a "Hindi" item, like
"8TH SCIENCE PUNJABI" seen on day 1) suggests a mislabel. Single-language resources only.
""")
d.code('''
mismatch = q("""
WITH titled AS (
  SELECT m.language, m.script, c.name,
    CASE
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Devanagari}') THEN 'Devanagari'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Gurmukhi}') THEN 'Gurmukhi'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Gujarati}') THEN 'Gujarati'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Kannada}') THEN 'Kannada'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Bengali}') THEN 'Bengali-Assamese'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Tamil}') THEN 'Tamil'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Telugu}') THEN 'Telugu'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Oriya}') THEN 'Odia'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Malayalam}') THEN 'Malayalam'
      WHEN REGEXP_CONTAINS(c.name, r'\\\\p{Arabic}') THEN 'Perso-Arabic'
      ELSE 'Latin/other'
    END AS title_script
  FROM `learning-reach-473210.staging.stg_diksha_content` AS c
  JOIN `learning-reach-473210.staging.stg_diksha_medium` AS m USING (identifier)
  WHERE m.identifier IN (
    SELECT identifier FROM `learning-reach-473210.staging.stg_diksha_medium`
    GROUP BY identifier HAVING COUNT(*) = 1)
    AND m.script IS NOT NULL AND m.language != 'English'
)
SELECT language, script AS expected_script,
       COUNT(*) AS resources,
       COUNTIF(title_script = script) AS title_in_expected_script,
       COUNTIF(title_script = 'Latin/other') AS title_in_latin,
       COUNTIF(title_script NOT IN (script, 'Latin/other')) AS title_in_other_indian_script
FROM titled GROUP BY language, script
HAVING resources >= 1000
ORDER BY language
""")
mismatch["pct_other_indian_script"] = 100 * mismatch["title_in_other_indian_script"] / mismatch["resources"]
mismatch.sort_values("pct_other_indian_script", ascending=False)
''')

d.md("""
## 6. YouTube: which videos keep getting views?

Daily history started 2 Oct 2026, so views-per-day becomes meaningful after a few days.
Until then, lifetime views per day since publishing is a rough proxy.
""")
d.code('''
yt = q("""
SELECT channel_title, title, is_hinglish, subject_group, days_since_publish, view_count,
       SAFE_DIVIDE(view_count, GREATEST(days_since_publish, 1)) AS lifetime_views_per_day,
       views_per_day
FROM `learning-reach-473210.marts.mart_youtube_performance`
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `learning-reach-473210.marts.mart_youtube_performance`)
""")
display(yt.groupby(["channel_title", "is_hinglish"])["lifetime_views_per_day"].median().to_frame())
display(yt.groupby("subject_group")["lifetime_views_per_day"].agg(["count", "median"]).sort_values("median", ascending=False))
''')

d.md("""
## Diagnostics summary (fill in)

- Biggest gaps (by gap points): …  Intense small gaps (by ratio): …
- Plan alignment (synthetic plan): …% of planned effort goes to already well-supplied cells.
- Usage-data coverage differs by …  → caveat for statistics.
- Teacher content: …% of all plays.
- Label quality: … has the highest share of titles in another Indian script.
""")
d.save(ROOT / "analysis" / "02_diagnostics.ipynb")


# ===================================================================== 03 statistics
s = Notebook()
s.md("""
# 03 · Statistics: multiple regression and hypothesis tests

All on **real** DIKSHA data (`marts.mart_content_features`), student content with usage data.

**Caveats that apply throughout**
- **Observational data:** results are *associations*, not causes.
- **Selection:** only resources with usage data (~82% of student content) — see `02_diagnostics` §3.
- **Portal plays only** (DIKSHA web), not app/offline use.
- **Huge n (~300K):** almost any difference is "statistically significant", so **effect sizes and
  confidence intervals matter more than p-values**.
""")
s.code(SETUP + '''
import statsmodels.formula.api as smf
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.multitest import multipletests
from scipy import stats
rng = np.random.default_rng(42)
''')

s.code('''
df = q("""
SELECT identifier, language_group, language, format, grade_band, subject, board_type,
       content_age_days, play_sessions, log_play_sessions, has_usage,
       avg_rating, ratings_count
FROM `learning-reach-473210.marts.mart_content_features`
WHERE NOT is_teacher_content
""")
print(len(df), "student resources;", int(df["has_usage"].sum()), "with usage")

# Keep the main subjects; group the long tail so the model stays readable.
main_subjects = ["Mathematics", "Science", "Social Science", "English", "Hindi",
                 "Environmental Studies", "Other languages"]
df["subject_g"] = np.where(df["subject"].isin(main_subjects), df["subject"], "Other/multiple")
df["log_age"] = np.log1p(df["content_age_days"])

model_df = df[df["has_usage"]
              & (df["language_group"] != "Unknown")
              & (df["format"].isin(["Video", "PDF", "Interactive"]))
              & (df["grade_band"] != "No class tag")
              & (df["board_type"] != "Other / none")].dropna(subset=["log_age"])
print(len(model_df), "rows in the regression")
''')

s.md("""
## 1. Multiple regression (OLS)

```
log(play_sessions + 1) ~ language group + format + grade band + subject + board type + log(content age)
```
Reference categories: English, PDF, Primary (1-5), Mathematics, National board.
Ratings are **not** used as a predictor: they are themselves a result of usage.

On a log outcome, coefficient *b* means roughly **(e^b − 1) × 100 % more plays** than the reference,
holding the other variables fixed. Standard errors are **HC3 heteroskedasticity-robust**.
""")
s.code('''
formula = ("log_play_sessions ~ C(language_group, Treatment('English')) + C(format, Treatment('PDF'))"
           " + C(grade_band, Treatment('Primary (1-5)')) + C(subject_g, Treatment('Mathematics'))"
           " + C(board_type, Treatment('National')) + log_age")
ols = smf.ols(formula, data=model_df).fit(cov_type="HC3")
print(f"n = {int(ols.nobs):,}   R² = {ols.rsquared:.3f}   adj. R² = {ols.rsquared_adj:.3f}")

table = pd.DataFrame({"coef": ols.params, "ci_low": ols.conf_int()[0], "ci_high": ols.conf_int()[1],
                      "p_value": ols.pvalues})
table["pct_effect"] = (np.exp(table["coef"]) - 1) * 100
table["pct_low"] = (np.exp(table["ci_low"]) - 1) * 100
table["pct_high"] = (np.exp(table["ci_high"]) - 1) * 100
table.index = (table.index.str.replace(r"C\\((\\w+).*?\\)\\[T\\.", r"\\1 = ", regex=True)
                          .str.replace("]", "", regex=False))
display(table[["pct_effect", "pct_low", "pct_high", "p_value"]].drop(index="Intercept").round(1))
''')

s.code('''
# Effect plot: % difference in plays vs the reference category, with 95% CI
t = table.drop(index=["Intercept", "log_age"]).sort_values("pct_effect")
plt.figure(figsize=(9, 0.35 * len(t) + 1))
plt.errorbar(t["pct_effect"], range(len(t)),
             xerr=[t["pct_effect"] - t["pct_low"], t["pct_high"] - t["pct_effect"]],
             fmt="o", color="#378ADD", ecolor="#888780")
plt.yticks(range(len(t)), t.index)
plt.axvline(0, color="black", linewidth=0.8)
plt.xlabel("% more (or fewer) plays than the reference, other factors equal (95% CI)")
plt.title("What is associated with more usage?")
plt.show()
''')

s.md("""
### Regression diagnostics
- **Residuals vs fitted** (sample): look for strong curvature or funnels.
- **Breusch–Pagan**: tests constant variance — expected to fail at this n; that is why HC3 SEs are used.
- **VIF**: multicollinearity; values above ~5–10 would make individual coefficients unreliable.
""")
s.code('''
sample = model_df.sample(n=min(20000, len(model_df)), random_state=42)
fitted = ols.predict(sample)
resid = sample["log_play_sessions"] - fitted

fig, axes = plt.subplots(1, 2, figsize=(14, 4))
axes[0].scatter(fitted, resid, s=3, alpha=0.3, color="#378ADD")
axes[0].axhline(0, color="black", linewidth=0.8)
axes[0].set_xlabel("fitted"); axes[0].set_ylabel("residual"); axes[0].set_title("Residuals vs fitted (20K sample)")
sm.qqplot(resid, line="s", ax=axes[1], markersize=2)
axes[1].set_title("Q-Q plot of residuals")
plt.show()

ols_s = smf.ols(formula, data=sample).fit()
bp_stat, bp_p, _, _ = het_breuschpagan(ols_s.resid, ols_s.model.exog)
print(f"Breusch-Pagan (sample): stat = {bp_stat:,.1f}, p = {bp_p:.2g}  -> heteroskedastic: use HC3 SEs")

X = pd.DataFrame(ols_s.model.exog, columns=ols_s.model.exog_names)
vif = pd.Series([variance_inflation_factor(X.values, i) for i in range(X.shape[1])], index=X.columns)
display(vif.drop("Intercept").sort_values(ascending=False).round(2).to_frame("VIF").head(10))
''')

s.md("""
### Sensitivity: who has usage data at all? (logistic regression)
If formats or languages differ strongly in *whether* usage is recorded, the OLS above (usage-only rows)
could be biased. Odds ratios > 1 = more likely to have usage data.
""")
s.code('''
logit_df = df[(df["language_group"] != "Unknown") & df["format"].isin(["Video", "PDF", "Interactive"])
              & (df["board_type"] != "Other / none")].dropna(subset=["log_age"])
logit_df = logit_df.sample(n=min(100000, len(logit_df)), random_state=42)
logit_df["has_usage_i"] = logit_df["has_usage"].astype(int)
logit = smf.logit("has_usage_i ~ C(language_group, Treatment('English')) + C(format, Treatment('PDF'))"
                  " + C(board_type, Treatment('National')) + log_age", data=logit_df).fit(disp=False)
odds = pd.DataFrame({"odds_ratio": np.exp(logit.params), "low": np.exp(logit.conf_int()[0]),
                     "high": np.exp(logit.conf_int()[1])}).drop(index="Intercept")
odds.round(2)
''')

s.md("""
## 2. Hypothesis tests

Each: hypotheses, test choice (and why), effect size with CI. α = 0.05, **Holm-corrected** across the
three primary tests. Play counts are extremely skewed, so tests 1–2 use **rank-based** tests.
""")
s.code('''
def rank_biserial(x, y):
    """Effect size for Mann-Whitney: P(x > y) - P(y > x), from -1 to 1."""
    u = stats.mannwhitneyu(x, y, alternative="two-sided").statistic
    return 2 * u / (len(x) * len(y)) - 1

def bootstrap_ci(func, x, y, n_boot=300, size=5000):
    """95% bootstrap CI of an effect size, on subsamples (keeps it fast at this n)."""
    vals = [func(rng.choice(x, min(size, len(x))), rng.choice(y, min(size, len(y))))
            for _ in range(n_boot)]
    return np.percentile(vals, [2.5, 97.5])

results = []
''')

s.md("""
### Test 1 — Regional-language vs English medium (state boards)
- **H0:** plays per resource have the same distribution for regional-language and English-medium content.
- **H1:** they differ.
- **Test:** Mann–Whitney U (two-sided): distributions are heavily skewed, so a t-test on raw plays is inappropriate.
- **Effect size:** rank-biserial correlation r (P(regional > English) − P(English > regional)).
""")
s.code('''
state = model_df[model_df["board_type"] == "State"]
regional = state.loc[state["language_group"].isin(["Hindi", "Other focus language", "Other regional language"]),
                     "play_sessions"].to_numpy()
english = state.loc[state["language_group"] == "English", "play_sessions"].to_numpy()

u_res = stats.mannwhitneyu(regional, english, alternative="two-sided")
r = rank_biserial(regional, english)
r_ci = bootstrap_ci(rank_biserial, regional, english)
print(f"n regional = {len(regional):,}, n English = {len(english):,}")
print(f"median plays: regional = {np.median(regional):,.0f}, English = {np.median(english):,.0f}")
print(f"U = {u_res.statistic:,.0f}, p = {u_res.pvalue:.2g}, rank-biserial r = {r:.3f} (95% CI {r_ci[0]:.3f} to {r_ci[1]:.3f})")
results.append(("1. Regional vs English (state boards)", u_res.pvalue, f"r = {r:.3f}"))
''')

s.md("""
### Test 2 — Does format matter? (Video vs PDF vs Interactive)
- **H0:** plays per resource have the same distribution across the three formats.
- **H1:** at least one format differs.
- **Test:** Kruskal–Wallis (rank-based ANOVA), then pairwise Mann–Whitney with **Holm** correction as post-hoc.
- **Effect size:** ε² for Kruskal–Wallis; rank-biserial r for each pair.
""")
s.code('''
groups = {f: model_df.loc[model_df["format"] == f, "play_sessions"].to_numpy()
          for f in ["Video", "PDF", "Interactive"]}
h, p_kw = stats.kruskal(*groups.values())
n_total = sum(len(g) for g in groups.values())
eps2 = (h - len(groups) + 1) / (n_total - len(groups))
print({f: f"median {np.median(g):,.0f} (n={len(g):,})" for f, g in groups.items()})
print(f"Kruskal-Wallis H = {h:,.1f}, p = {p_kw:.2g}, epsilon² = {eps2:.3f}")
results.append(("2. Format (Kruskal-Wallis)", p_kw, f"epsilon² = {eps2:.3f}"))

pairs = [("Interactive", "Video"), ("Interactive", "PDF"), ("Video", "PDF")]
raw_p = [stats.mannwhitneyu(groups[a], groups[b], alternative="two-sided").pvalue for a, b in pairs]
holm_p = multipletests(raw_p, method="holm")[1]
display(pd.DataFrame({"pair": [f"{a} vs {b}" for a, b in pairs],
                      "rank_biserial_r": [rank_biserial(groups[a], groups[b]) for a, b in pairs],
                      "p_holm": holm_p}))
''')

s.md("""
### Test 3 — Ratings: focus languages vs other regional languages
- **H0:** mean rating is equal for focus-language and other-regional-language content.
- **H1:** the means differ.
- **Test:** Welch's t-test (unequal variances). Ratings are bounded 1–5 and averaged over ≥ 10 ratings,
  so means are reasonable here; Welch avoids assuming equal variances.
- **Effect size:** Cohen's d with 95% CI for the mean difference.
""")
s.code('''
rated = df[(df["ratings_count"] >= 10) & df["avg_rating"].notna()]
focus = rated.loc[rated["language_group"].isin(["Hindi", "Other focus language"]), "avg_rating"].to_numpy()
other = rated.loc[rated["language_group"] == "Other regional language", "avg_rating"].to_numpy()

t_res = stats.ttest_ind(focus, other, equal_var=False)
diff = focus.mean() - other.mean()
se = np.sqrt(focus.var(ddof=1) / len(focus) + other.var(ddof=1) / len(other))
dof = se**4 / ((focus.var(ddof=1) / len(focus))**2 / (len(focus) - 1)
               + (other.var(ddof=1) / len(other))**2 / (len(other) - 1))
ci = diff + np.array([-1, 1]) * stats.t.ppf(0.975, dof) * se
pooled_sd = np.sqrt(((len(focus) - 1) * focus.var(ddof=1) + (len(other) - 1) * other.var(ddof=1))
                    / (len(focus) + len(other) - 2))
d_cohen = diff / pooled_sd
print(f"n focus = {len(focus):,}, n other = {len(other):,}")
print(f"mean rating: focus = {focus.mean():.3f}, other = {other.mean():.3f}, diff = {diff:.3f} (95% CI {ci[0]:.3f} to {ci[1]:.3f})")
print(f"Welch t = {t_res.statistic:.2f}, p = {t_res.pvalue:.2g}, Cohen's d = {d_cohen:.3f}")
results.append(("3. Ratings focus vs other regional", t_res.pvalue, f"d = {d_cohen:.3f}"))
''')

s.md("""
### Multiple comparisons
Three primary tests → Holm correction keeps the family-wise error rate at 5%.
Rule of thumb for effect sizes: |r| or d ≈ 0.1 small, 0.3 medium, 0.5 large; ε² ≈ 0.01 small, 0.06 medium, 0.14 large.
""")
s.code('''
summary = pd.DataFrame(results, columns=["test", "p_value", "effect_size"])
summary["p_holm"] = multipletests(summary["p_value"], method="holm")[1]
summary["reject_H0"] = summary["p_holm"] < 0.05
summary
''')

s.md("""
## Interpretation (fill in after running)

- **Regression:** holding other factors equal, interactive content gets about …% more plays than PDFs;
  focus-language content …% vs English; … R² = … (most variation is unexplained — expected for usage data).
- **Test 1:** regional vs English: r = … (small/medium) → …
- **Test 2:** format: ε² = … → …
- **Test 3:** ratings: d = … → statistically significant but practically small/large?
- **Caveats:** associations only; usage-data selection; portal plays only.
""")
s.save(ROOT / "analysis" / "03_statistics.ipynb")
