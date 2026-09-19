# Study Sentinel — Stage 3 Long-Term Surveillance Report

**Surveillance Period**: Cuts 1–12  
**Generated**: 2026-09-19T17:25:18.531599  
**Operating Status**: COMPLETED across 12 sequential cuts

---

## 1. Executive Summary

- **Total Findings Evaluated**: 2065 (56 serious / critical)
- **Data Queries Dispatched**: 40
- **Quarantined Investigational Sites**: S11 (data preserved without deletion)
- **Untrusted Laboratory Records**: 137 (conversion shifts flagged, clinical escalations suppressed)
- **Protocol Amendments Handled**: 2 transition(s)
- **Final Resource Status**: FULL tier (18.2% budget utilized)

---

## 2. Cut-by-Cut Progression Ledger

| Cut | Protocol | New Records | Corrections | Findings | Escalations | Queries | Budget Used | Tier |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | v1 | 4,509 | 0 | 38 | 0 | 0 | 712.5 ms | FULL |
| 2 | v1 | 2,773 | 0 | 50 | 0 | 0 | 620.8 ms | FULL |
| 3 | v1 | 1,477 | 0 | 55 | 0 | 0 | 888.2 ms | FULL |
| 4 | v1 | 2,027 | 0 | 53 | 0 | 0 | 1030.3 ms | FULL |
| 5 | v2 | 1,430 | 200 | 116 | 1 | 0 | 1200.7 ms | FULL |
| 6 | v2 | 3,354 | 0 | 188 | 5 | 9 | 1688.0 ms | FULL |
| 7 | v2 | 1,695 | 0 | 203 | 3 | 0 | 1809.8 ms | FULL |
| 8 | v2 | 1,384 | 0 | 219 | 1 | 1 | 2113.8 ms | FULL |
| 9 | v3 | 2,248 | 0 | 242 | 3 | 0 | 2587.8 ms | FULL |
| 10 | v3 | 1,471 | 0 | 262 | 3 | 0 | 2614.0 ms | FULL |
| 11 | v3 | 3,342 | 0 | 316 | 1 | 15 | 3282.9 ms | FULL |
| 12 | v3 | 772 | 0 | 323 | 0 | 15 | 3313.2 ms | FULL |

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
