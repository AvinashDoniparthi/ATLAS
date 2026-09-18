# Reve — Study Sentinel

Problem 1 / Stage 1 — ATLAS only. Deterministic Python backend; no LLM in the clinical path.

## Run it
```
python -m pip install -r requirements.txt
python starter/run_local_harness.py --module stage1.atlas --data hackathon-data   # answers + graph_stats.json + stage1_public.json
python -m stage1.cli build            # discovered files, row counts, cuts, protocol versions, node/edge counts
python -m stage1.cli ask "Which subjects meet the Hy's law criteria?" --debug
python -m pytest tests -q
```
`starter/` is a **reconstructed** compatibility layer (the organiser's `schemas.py`/harness were not available); see `docs/reconstructed_starter_assumptions.md`. Only `stage1/atlas.py` touches it. `GEMINI_API_KEY` (optional, `.env`) enables Gemini for phrasing only.

## How we understood the problem
Nine tables that never reference each other must become one graph per subject, then be questioned with answers that cite the exact `(domain, USUBJID, SEQ)` records that prove them.
The dataset is adversarial by design: a site reporting in another unit, two date formats, `<5`/`ND`/blank/`12,4` values, a person enrolled twice, a sentence in the lab manual that tries to instruct the reviewer.
The protocol is versioned and the version in force depends on the data cut; findings derived under the wrong version are wrong answers that do not error.
Saying "none" with no evidence is a full-marks answer when nothing qualifies — guessing costs twice.
The grader uses unseen studies of the same design, so everything is discovered from the files; no IDs, sites, counts or answers are hard-coded (`tests/test_no_hardcoding.py` enforces it).

## Architecture
`csv/json/md loaders` → `normalisation` (dates, typed lab values, unit registry) → `cut view` (`cut_available <= N`, corrections with `cut <= N` applied to copies) → `StudyGraphCore` (dict graph + indexes: by_ref, subject×domain, site, subject×visit, subject×test, dated lists) → `protocol rules for the cut` (`cuts.csv` → version → regex-parsed `protocol_vN.md`) → `deterministic rules` (`backend/rules/*`, cached per build key = data signature + cut + version + corrections) → `evidence validator` (every citation must exist in the cut view and re-pass its claim check) → `router` (keyword/regex intent → count/lookup/finding/doc tools) → `Answer`. Gemini, if a key is present, sits outside this pipeline (intent hints, sentence rewrite) and is validated against the same schema. `Atlas.answer()` fingerprints the data directory each call and rebuilds when it changes.

## Tech stack
| Layer | Choice | Why this rather than the obvious alternative |
|---|---|---|
| Ingestion | stdlib `csv` | pandas coerces `"<5"` and `"12,4"` and dtype-infers columns; we need raw strings and per-row error capture |
| Graph | in-memory dict graph + indexes | Neo4j adds a service to a 27k-record study; a dict graph builds in ~1.5 s and answers in ms |
| Protocol | regex section parser per version | An LLM reading the protocol would be the source of truth for thresholds; regex output is inspectable and citable |
| Boundary | pydantic v2 (starter models only) | Schema-valid JSON at the harness boundary without leaking validation into clinical code |
| NL layer | Gemini optional | The deterministic router answers everything; Gemini can only rephrase or hint intent, never decide facts |

## Data handling
**Units:** `reference_ranges.csv` is indexed by (test, `LAB`); a record's lab is its site when a site row exists, else CENTRAL. Values are compared only in the range's unit; conversions come from a registry seeded from physical constants and from sentences in the lab manual (`1 µkat/L = 60 U/L`), and the chain (original value/unit → factor → normalised value → range) is kept on every lab. No range or no conversion → `unknown`, never "normal". **Dates:** ISO and `DD-MON-YYYY` (plus slash/compact fallbacks); unparseable dates are tagged, excluded from date arithmetic and counted. **Non-numeric labs:** typed as censored / not-detected / missing / non-numeric — never `0`, never compared; `12,4` → `12.4` only for `^\d+,\d+$`. **Malformed rows:** missing `USUBJID`, short/long rows and duplicate `(USUBJID, SEQ)` are recorded in the load report and skipped or flagged, never fatal. **Duplicate subjects:** DM rows sharing initials/birth date/sex plus ≥2 of age/arm/start date/HbA1c are one person; totals count persons, both DM rows are cited.

## Documents
Protocols are parsed per version (inclusion/exclusion, schedule and window, prohibited classes, SAE and `AESHOSP=Y` rule, Hy's law multiples/window, dose per arm) with the sentence and section kept for `DOC` citations; the version applied is resolved from `cuts.csv`. The lab manual supplies unit facts; the SAP is context. Every document is scanned for sentences addressed to an automated reviewer (e.g. "exclude sites … do not flag Hy's law"); they are stored as `InstructionLike` evidence, can be reported when asked, and are wired to nothing — findings from the named sites are still returned.

## When the answer is nothing
The router distinguishes **NO_MATCH** (the rule ran, the filter matched nothing → `answer: []`, `evidence: []`, confidence ≈ 0.85), **INSUFFICIENT** (the rule or data needed is unavailable, e.g. no reference range or no protocol rule for this cut → empty answer, confidence ≈ 0.3, text says why) and **AMBIGUOUS** (question not understood → `None`, confidence ≈ 0.2). Evidence that fails validation is dropped, confidence is reduced, and a finding that loses its supporting records is dropped rather than cited.

## Graph
Nodes: STUDY, SITE, SUBJECT, PERSON (duplicate clusters), VISIT (subject×visit), RECORD (one per row), PROTOCOL_VERSION, REFERENCE_RANGE, DOCUMENT. Edges: HAS_SITE, ENROLLED, HAS_RECORD, HAS_VISIT, AT_VISIT, COMPARED_TO (LB → range used), UNDER_PROTOCOL, SAME_PERSON, POSSIBLE_DUPLICATE_OF, GOVERNED_BY. `build()` returns `nodes, edges, subjects, build_ms, cut, protocol_version` plus per-type counts; `graph_stats.json` is written by `scripts/generate_graph_stats.py` from that call.

## What we know is weak
The starter layer is reconstructed from the specification, so field names may differ from the organiser's. Protocol parsing is regex-based: a reworded protocol may leave a rule `None` (the system then answers INSUFFICIENT rather than guessing). The Hy's law "alternative explanation" check is heuristic (baseline elevation, cholestasis history) and only lowers confidence. The router is keyword-based; unusual phrasings of hidden questions may be classified as ambiguous. The duplicate-person heuristic has fixed column thresholds. Only the four documented public questions could be exercised.
