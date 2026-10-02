# Where Indian-language learners are underserved — and what content gets used

**For:** content & partnerships lead · **Data as of:** 2 Oct 2026 · **Source:** DIKSHA (Government of India school-content platform, ~430K live learning resources), YouTube, UNESCO UIS, World Bank — public data, refreshed daily by an automated GCP pipeline. Not affiliated with DIKSHA or Khan Academy.

---

## Three findings

**1. The gaps are specific and concentrated.**
Comparing each class × subject's share of plays (demand) with its share of resources (supply), within each language:
- **Kannada, primary Maths (Classes 1–5):** demand up to **13×** supply (Class 3: 93 resources).
- **Punjabi, English (Classes 6–11):** only **10–16 resources per class**, demand **6–13×** supply.
- **Marathi, English and language subjects (Classes 2–10):** consistently 2–3 points more demand than supply.
- **Assamese is the least complete focus language** (**63%** of class × subject cells have ≥ 10 resources, vs 100% for Hindi and English): English has 1–6 resources per class and Hindi has none. Punjabi is next (**73%**), then Marathi (76%), Kannada (82%), Gujarati (97%).

**2. Local-language and interactive content work harder.**
Holding grade, subject, format, board and age equal, content in Marathi, Gujarati, Kannada, Punjabi or Assamese gets **about 2× the plays** of English content (Hindi: **+47%**). **Interactive** resources get **+47%** more plays than PDFs (medians 207 vs 48) — the clearest effect in the statistical tests. Raw language differences are real but modest (most content overlaps), so language is a lever, not a guarantee.

**3. Attention is concentrated, and planning doesn't follow the gaps yet.**
The top **1% of resources get 54% of all plays**; teacher-training content is 9% of resources but **59% of plays**, so student demand must be measured separately. In the sample localization plan, **~38% of planned effort** (9 of 22 plans, 345 of 915 planned resources) goes to cells that are already well supplied, while none of the top 10 Marathi gaps is planned.

## Three recommendations

1. **Re-point the plan at the gap list.** Move effort from "already well supplied" plans to Kannada primary Maths, Punjabi English (6–11) and Marathi English. Review `mart_unplanned_gaps` monthly: it lists the top 10 open gaps per language automatically.
2. **Make new local-language content interactive.** Prioritise practice/interactive formats over PDFs when localizing; reuse topics that already perform well rather than translating whole textbooks.
3. **Treat Assamese and Punjabi as coverage priorities, and fix measurement.** Start with Assamese English and Punjabi primary. Report student and teacher usage separately, and close tracking gaps (usage is missing for ~46% of union-territory-board items and ~41% of items with no language tag).

---

> **Caveats**
> - **Associations, not causes:** observational data; the "2×" is what goes with local-language content, not proof that translating causes it.
> - **Partial usage data:** DIKSHA web-portal plays only; ~82% of student resources report usage, and Hindi, multilingual and interactive items are *less* likely to report it — so their effects may be overstated slightly.
> - A resource tagged with several classes or subjects counts in each; curriculum rules remove impossible cells (e.g. Science in Class 2).
> - The localization plan is **synthetic** (it demonstrates the method); all DIKSHA, YouTube and indicator figures are real.
> - Daily usage history starts on 1 Oct 2026; trends need a few weeks.

*Method: `analysis/01_eda`, `02_diagnostics`, `03_statistics` (OLS on log plays, n = 300,316, R² = 0.30, robust SEs; Mann–Whitney, Kruskal–Wallis, Welch tests with Holm correction).*
