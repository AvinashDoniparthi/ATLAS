# Study Sentinel — Stage 3 Long-Term Surveillance Report

**Surveillance Period**: Cuts 1–12  
**Generated**: 2026-09-19T19:07:07.713111  
**Operating Status**: COMPLETED across 12 sequential cuts

---

## 1. Executive Summary

- **Total Findings Evaluated**: 4130 (108 serious / critical)
- **Data Queries Dispatched**: 0
- **Quarantined Investigational Sites**: S11 (data preserved without deletion)
- **Untrusted Laboratory Records**: 137 (conversion shifts flagged, clinical escalations suppressed)
- **Protocol Amendments Handled**: 5 transition(s)
- **Final Resource Status**: ESSENTIAL tier (100.0% budget utilized)

---

## 2. Cut-by-Cut Progression Ledger

| Cut | Protocol | New Records | Corrections | Findings | Escalations | Queries | Budget Used | Tier |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | v1 | 4,509 | 0 | 38 | 0 | 0 | 497.0 ms | ESSENTIAL |
| 2 | v1 | 2,773 | 0 | 50 | 0 | 0 | 555.0 ms | ESSENTIAL |
| 3 | v1 | 1,477 | 0 | 55 | 0 | 0 | 1967.4 ms | ESSENTIAL |
| 4 | v1 | 2,027 | 0 | 53 | 0 | 0 | 2692.3 ms | ESSENTIAL |
| 5 | v2 | 1,430 | 200 | 116 | 0 | 0 | 3812.2 ms | ESSENTIAL |
| 6 | v2 | 3,354 | 0 | 188 | 0 | 0 | 4896.6 ms | ESSENTIAL |
| 7 | v2 | 1,695 | 0 | 203 | 0 | 0 | 5430.4 ms | ESSENTIAL |
| 8 | v2 | 1,384 | 0 | 219 | 0 | 0 | 6006.9 ms | ESSENTIAL |
| 9 | v3 | 2,248 | 0 | 242 | 0 | 0 | 6347.0 ms | ESSENTIAL |
| 10 | v3 | 1,471 | 0 | 262 | 0 | 0 | 6826.3 ms | ESSENTIAL |
| 11 | v3 | 3,342 | 0 | 316 | 0 | 0 | 7855.7 ms | ESSENTIAL |
| 12 | v3 | 772 | 0 | 323 | 0 | 0 | 7676.9 ms | ESSENTIAL |
| 1 | v1 | 4,509 | 0 | 38 | 0 | 0 | 556.9 ms | ESSENTIAL |
| 2 | v1 | 2,773 | 0 | 50 | 0 | 0 | 1653.7 ms | ESSENTIAL |
| 3 | v1 | 1,477 | 0 | 55 | 0 | 0 | 1941.6 ms | ESSENTIAL |
| 4 | v1 | 2,027 | 0 | 53 | 0 | 0 | 2737.6 ms | ESSENTIAL |
| 5 | v2 | 1,430 | 200 | 116 | 0 | 0 | 3621.6 ms | ESSENTIAL |
| 6 | v2 | 3,354 | 0 | 188 | 0 | 0 | 5270.6 ms | ESSENTIAL |
| 7 | v2 | 1,695 | 0 | 203 | 0 | 0 | 5969.5 ms | ESSENTIAL |
| 8 | v2 | 1,384 | 0 | 219 | 0 | 0 | 5701.6 ms | ESSENTIAL |
| 9 | v3 | 2,248 | 0 | 242 | 0 | 0 | 7500.5 ms | ESSENTIAL |
| 10 | v3 | 1,471 | 0 | 262 | 0 | 0 | 7415.9 ms | ESSENTIAL |
| 11 | v3 | 3,342 | 0 | 316 | 0 | 0 | 9107.7 ms | ESSENTIAL |
| 12 | v3 | 772 | 0 | 323 | 0 | 0 | 8281.7 ms | ESSENTIAL |

---

## 3. Human Medical Monitor Adjudication Summary

| Status | Count | Description |
|:---|:---:|:---|
| **APPROVED** | 5 | Adjudicated and confirmed by human monitor or verified after clarification |
| **REJECTED** | 1 | Rejected by human monitor; downgraded to monitoring without re-escalation |
| **STANDING_LIMITS** | 7 | Transitioned to protocol safety limits after 4 unanswered cuts |
| **PENDING** | 17 | Currently awaiting human adjudication under response delay policy |
| **UNRESOLVED** | 0 | Clarification question unverified from trial source data |

---

## 4. Adversarial and Data Integrity Defenses

### Laboratory Unit & Distribution Shift Detection
- Statistical median ratio monitoring active across all laboratory analytes.
- Identified conversion factor drops (e.g. glucose 18.016x ratio change) without unit changes in data cuts.
- Flagged 137 record(s) as untrusted; suppressed clinical escalations that rest solely on uncalibrated lab values.

### Site Regularity Anomalies
- Variance monitoring detected sites exhibiting cross-signal variance an order of magnitude below study variance.
- Quarantined sites: **S11**.
- In accordance with GCP and 21 CFR Part 11, site records were preserved in full and not deleted from the database.

### Document Tampering & Prompt Injection Resistance
- Document sha256 registry active across all protocol and manual versions.
- Detected 0 document state change(s).
- All embedded instructional overrides (e.g. directives to automated reviewers) were logged as factual metadata and explicitly **NOT executed**.

---

## 5. Audit Trace and Explainability

All decisions are linked to immutable `WatchTraceEntry` audit records written at decision time. Every decision can be independently queried and audited via `StudyWatch.explain(decision_id)`.
