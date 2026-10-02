# 0004: Use DIKSHA `medium`, not `language`, as the language field

**Status:** accepted · 1 Oct 2026

## Context
DIKSHA has two language-like fields. `language` says "English" on 423,821 of about 430K resources (a form default, including Hindi and Marathi content). `medium` is the language of instruction and has real values, but with case variants (`PUNJABI` / `Punjabi`), misspellings (`Oriya`, `Sankrit`, `Konakni`) and non-languages (`Other`, `Languages`).

## Decision
Use `medium` as the language. Normalise it in staging with a mapping table (`staging.language_map`, about 37 variants to standard names, plus script and a focus-language flag). Resources with no usable value become `Unknown`.

An assertion (`assert_medium_unmapped`) fails the run if any value used by 100 or more resources is unmapped.

## Consequences
- Language analysis reflects who the content is for.
- A title-script check on a sample found at most 4.7% of titles in another Indian script (Urdu), under 1.5% for most languages, so `medium` is usable.
- `medium` is a list, so a resource can have several languages. The regression groups those as "Multilingual".
- About 5% of resources have no `medium` and appear as Unknown.

Evidence: `docs/data_dictionary.md`, `docs/ingestion_design.md` test 16.
