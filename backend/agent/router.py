"""Deterministic question router.

QUESTION -> Intent (regex/keyword classification + entity extraction)
         -> deterministic tool(s) from backend.queries
         -> validated evidence (backend.evidence)
         -> InternalAnswer

An optional LLM may refine the Intent when the deterministic classifier is
unsure, and may rephrase the final sentence; it never produces facts.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from backend.agent.schemas import (
    STATUS_AMBIGUOUS,
    STATUS_INSUFFICIENT,
    STATUS_NO_MATCH,
    STATUS_OK,
    InternalAnswer,
    QueryTrace,
)
from backend.agent.tools import TOOLS
from backend.evidence.evidence_engine import assemble, assemble_items, confidence_for
from backend.evidence.record_refs import dedupe_sorted
from backend.evidence.validator import validate_refs
from backend.graph.nodes import DocRefKey, EvidenceItem, RecordKey
from backend.normalization.dates import parse_date
from backend.protocol.protocol_loader import normalize_class_token, normalize_visit_code
from backend.queries.counts import CountFilters
from backend.queries.documents import sites_mentioned
from backend.queries.findings import KNOWN_CODES, label as finding_label
from backend.queries.lookups import domains_from_text

log = logging.getLogger("atlas.router")

# --------------------------------------------------------------------------- #
# Intent
# --------------------------------------------------------------------------- #
FINDING_KEYWORDS: list[tuple[str, str, Optional[str]]] = [
    # (regex, code, subcode)
    (r"hy'?s?\s*law|hys[- ]law|drug[- ]induced liver|liver[- ](?:injury|signal|damage)|dili\b", "HYS_LAW_CANDIDATE", None),
    (r"miscod|mis-coded|coded as non-serious|aeser.*(?:wrong|incorrect)", "SERIOUS_AE", "SAE_MISCODED"),
    (r"serious adverse|\bsaes?\b|hospitali[sz]", "SERIOUS_AE", None),
    (r"before (?:the |their |his |her |any )?(?:first |initial )?(?:dose|dosing|treatment|exposure)|pre[- ](?:treatment|dose|dosing) (?:adverse|ae|event)|prior to (?:the |their )?(?:first |initial )?dos|not treatment[- ]emergent|non[- ]treatment[- ]emergent|before (?:study )?(?:drug|treatment) start", "AE_BEFORE_FIRST_DOSE", None),
    (r"no exposure|never dosed|undosed|without any (?:exposure|dose|dosing)|no (?:dose|dosing|exposure) records? at all|zero (?:exposure|dose) records?|not dosed at all", "MISSING_DOSE", "NO_EXPOSURE"),
    (r"missing (?:an? |any |their )?(?:dose|dosing|exposure)|missed (?:an? |any )?(?:dose|dosing visit)|without (?:an? )?(?:exposure|dose) record|no (?:dose|dosing|exposure) records?|(?:dose|exposure) records? (?:is |are )?missing|absent (?:dose|exposure)", "MISSING_DOSE", None),
    (r"wrong dose|dosing error|incorrect dose|dose error|overdos|underdos|mis-?dos|dose deviation|wrong amount", "DOSING_ERROR", None),
    (r"visit[- ]windows?|out[- ]of[- ]window|outside (?:of )?(?:the |their |a )?(?:visit |protocol |scheduled |allowed |permitted )*windows?|late visits?|early visits?|window deviation|visit deviations?|off[- ]schedule|(?:visits?|scheduled) (?:that (?:were|was) )?(?:late|early|out of|outside)|scheduling deviation|visit timing", "VISIT_WINDOW_DEVIATION", None),
    (r"prohibited|disallowed|forbidden|banned|not permitted|restricted medication", "PROHIBITED_MEDICATION", None),
    (r"eligib|inclusion|exclusion|ineligible|under[- ]?age|too young|too old|should not have been enrolled|screen(?:ing)? fail", "ELIGIBILITY_VIOLATION", None),
    (r"duplicate|enrolled twice|double[- ]enrol|same person|twice enrolled", "DUPLICATE_SUBJECT", None),
]

DOC_KEYWORDS = r"protocol (?:say|state|require|define|version)|\bin the protocol\b|\bper (?:the )?protocol\b|\baccording to (?:the )?protocol\b|\bwhat (?:is|are) the (?:\w+ ){0,3}(?:window|threshold|criteri|rule|definition|schedule|dose|limit)|what does the (?:protocol|lab[- ]manual|laboratory manual|sap|statistical analysis plan)|lab(?:oratory)? manual|instruction|automated reviewer|addressed to|amendment|differ(?:ence|s)? between (?:protocol )?version|which (?:protocol )?version|sap\b"
COUNT_KEYWORDS = r"\bhow many\b|\bnumber of\b|\bcount\b|\btotal (?:number|of)\b|\bhow much\b"
LOOKUP_KEYWORDS = r"\blist\b|\bshow\b|\brecords?\b|\bgive me\b|\bretrieve\b|\bfetch\b|\bwhat (?:are|were) the\b|\bdisplay\b"
P360_KEYWORDS = r"patient ?360|everything (?:about|for|on)|full profile|profile of|summar(?:y|ise|ize) (?:of )?subject|all data for"
MAX_LISTED_SUBJECTS = 10  # display cap for enumerating subject ids in answer text
SUBJECT_WORDS = r"\bsubjects?\b|\bpatients?\b|\bparticipants?\b|\bpeople\b|\bpersons?\b|\bindividuals?\b"

TEST_WORDS = {
    "bilirubin": "BILI", "bili": "BILI", "tbil": "BILI", "creatinine": "CREAT", "creat": "CREAT",
    "glucose": "GLUC", "gluc": "GLUC", "hba1c": "HBA1C", "alt": "ALT", "ast": "AST",
    "alanine aminotransferase": "ALT", "aspartate aminotransferase": "AST",
}

VISIT_PATTERNS = [
    (r"\b(?:week|wk)\s*[-_]?\s*(\d+)\b", lambda m: f"WEEK{int(m.group(1))}"),
    (r"\bw(\d+)\b", lambda m: f"WEEK{int(m.group(1))}"),
    (r"\bbaseline\b", lambda m: "BASELINE"),
    (r"\bscreening\b", lambda m: "SCREENING"),
    (r"\beos\b|\bend[- ]of[- ](?:study|treatment)\b|\bfinal visit\b", lambda m: "EOS"),
    (r"\bday\s*[-−]?\s*(\d+)\b", lambda m: f"DAY{int(m.group(1))}"),
]


@dataclass
class Intent:
    kind: str = "unknown"          # count | lookup | finding | doc | patient360 | unknown
    confidence: float = 0.0
    count_target: str = "subjects"  # subjects | records
    domain: Optional[str] = None    # for record counts
    subject: Optional[str] = None
    site: Optional[str] = None
    visit: Optional[str] = None
    window_days: Optional[int] = None
    domains: list[str] = field(default_factory=list)
    testcd: Optional[str] = None
    finding_code: Optional[str] = None
    subcode: Optional[str] = None
    filters: dict[str, Any] = field(default_factory=dict)
    topic: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, [], {}, "") and k not in ("confidence", "notes")}
        d["date_from"] = self.date_from.isoformat() if self.date_from else None
        d["date_to"] = self.date_to.isoformat() if self.date_to else None
        return {k: v for k, v in d.items() if v is not None}


ALLOWED_LLM_KEYS = {
    "kind", "count_target", "domain", "site", "subject", "visit", "window_days", "domains", "testcd",
    "finding_code", "subcode", "disposition", "disposition_reason", "ae_serious", "ae_term", "arm", "sex",
    "age_min", "age_max", "cm_class", "prohibited_med", "topic",
}


# --------------------------------------------------------------------------- #
class Router:
    def __init__(self, core):
        self.core = core
        self._subject_re = self._derive_subject_regex()

    # ------------------------------------------------------------ utilities
    def _derive_subject_regex(self) -> re.Pattern:
        pats = [r"\b\d{3}-S\d{2}-\d{3}\b"]
        subjects = list(getattr(self.core.idx, "subjects", []) or [])
        if subjects:
            sample = subjects[0]
            parts = []
            for m in re.finditer(r"[A-Za-z]+|\d+|[^A-Za-z0-9]+", sample):
                tok = m.group(0)
                if tok.isdigit():
                    parts.append(rf"\d{{{len(tok)}}}")
                elif tok.isalpha():
                    parts.append(rf"[A-Za-z]{{{len(tok)}}}")
                else:
                    parts.append(re.escape(tok))
            derived = r"\b" + "".join(parts) + r"\b"
            if derived not in pats:
                pats.append(derived)
        return re.compile("|".join(f"(?:{p})" for p in pats))

    def _known_sites(self) -> dict[str, str]:
        sites = {s.upper(): s for s in getattr(self.core.idx, "by_site", {}).keys()}
        return sites

    def _resolve_site(self, token: str) -> Optional[str]:
        token = token.strip().upper()
        sites = self._known_sites()
        if token in sites:
            return sites[token]
        digits = re.sub(r"\D", "", token)
        if digits:
            for s in sites:
                sd = re.sub(r"\D", "", s)
                if sd and int(sd) == int(digits):
                    return sites[s]
            # site not present in this study — still a valid filter (answer will be empty)
            return f"S{int(digits):02d}" if not token.startswith("S") else token
        return None

    def _known_tests(self) -> set[str]:
        tests = set(self.core.ref_ranges.tests()) if getattr(self.core, "ref_ranges", None) else set()
        for (dom, t) in getattr(self.core.idx, "by_test", {}).keys():
            tests.add(t)
        return {t.upper() for t in tests}

    # --------------------------------------------------------- classification
    def classify(self, text: str, kind_hint: Optional[str] = None) -> Intent:
        it = Intent()
        raw = text or ""
        low = raw.lower()

        # subjects (remove from text so site regex does not match inside them)
        m = self._subject_re.search(raw)
        if m:
            it.subject = m.group(0)
            if it.subject not in self.core.idx.by_subject:
                # case-insensitive rescue
                for u in self.core.idx.subjects:
                    if u.lower() == it.subject.lower():
                        it.subject = u
                        break
        stripped = self._subject_re.sub(" ", raw)
        low_s = stripped.lower()

        # site
        sm = re.search(r"\bsites?\s+(?:id\s+)?(S?\d{1,3})\b", stripped, re.I) or re.search(r"\b(S\d{2,3})\b", stripped)
        if sm:
            it.site = self._resolve_site(sm.group(1))

        # visit / window
        for pat, fn in VISIT_PATTERNS:
            vm = re.search(pat, low_s)
            if vm:
                it.visit = fn(vm)
                break
        wm = re.search(r"within\s+(?:±\s*)?(\d+)\s+days?", low_s) or re.search(r"(?:±|\+/-)\s*(\d+)\s*days?", low_s)
        if wm:
            it.window_days = int(wm.group(1))

        # dates
        dts = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}-[A-Za-z]{3}-\d{4}\b", stripped)
        parsed = [parse_date(d) for d in dts]
        parsed = [p.date for p in parsed if p.ok and p.date]
        if len(parsed) >= 2:
            it.date_from, it.date_to = min(parsed), max(parsed)
        elif len(parsed) == 1:
            if re.search(r"\bafter\b|\bsince\b|\bfrom\b", low_s):
                it.date_from = parsed[0]
            elif re.search(r"\bbefore\b|\buntil\b|\bup to\b", low_s):
                it.date_to = parsed[0]

        # tests
        known = self._known_tests()
        for word, code in TEST_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", low_s) and (code in known or not known):
                it.testcd = code
                break
        if it.testcd is None:
            for t in sorted(known, key=len, reverse=True):
                if re.search(rf"\b{re.escape(t.lower())}\b", low_s):
                    it.testcd = t
                    break

        # domains
        it.domains = domains_from_text(stripped, self.core.idx.domains)

        # finding code
        for pat, code, sub in FINDING_KEYWORDS:
            if re.search(pat, low_s):
                it.finding_code, it.subcode = code, sub
                break

        # generic filters
        f: dict[str, Any] = {}
        if re.search(r"\bdiscontinu|\bwithdr|\bdropped out|\bdrop-?out|\bearly termination|\bterminated early", low_s):
            f["disposition"] = "DISCONTINUED"
            if re.search(r"adverse|\bae\b|\baes\b", low_s):
                f["disposition_reason"] = "ADVERSE EVENT"
            elif re.search(r"lost to follow", low_s):
                f["disposition_reason"] = "LOST TO FOLLOW"
            elif re.search(r"withdr(?:awal|ew|awn) by subject|subject withdr|consent", low_s):
                f["disposition_reason"] = "WITHDRAWAL BY SUBJECT"
            if it.finding_code == "SERIOUS_AE" and "disposition_reason" in f:
                it.finding_code, it.subcode = None, None  # "discontinued due to AE" is a disposition question
        elif re.search(r"\bcompleted\b|\bfinished\b|\bcompleters\b", low_s):
            f["disposition"] = "COMPLETED"
        if re.search(r"\bplacebo\b", low_s) and not it.finding_code == "DOSING_ERROR":
            f["arm"] = "PLACEBO"
        elif re.search(r"\b(?:drug|active|treatment)\s+(?:arm|group)\b|\bon (?:active )?drug\b", low_s):
            f["arm"] = "DRUG"
        if re.search(r"\bfemales?\b|\bwomen\b|\bwoman\b", low_s):
            f["sex"] = "F"
        elif re.search(r"\bmales?\b|\bmen\b|\bman\b", low_s):
            f["sex"] = "M"
        am = re.search(r"\b(?:under|younger than|below)\s+(\d{1,3})\b", low_s)
        if am:
            f["age_max"] = float(am.group(1)) - 1
        am = re.search(r"\b(?:over|older than|above)\s+(\d{1,3})\b", low_s)
        if am and "years" in low_s or (am and re.search(r"\bage", low_s)):
            f["age_min"] = float(am.group(1)) + 1
        am = re.search(r"\baged?\s+(\d{1,3})\s*(?:-|to|–)\s*(\d{1,3})", low_s)
        if am:
            f["age_min"], f["age_max"] = float(am.group(1)), float(am.group(2))
        if it.finding_code == "SERIOUS_AE" and it.subcode is None:
            f["ae_serious"] = True
        if it.finding_code == "PROHIBITED_MEDICATION":
            f["prohibited_med"] = True
        # medication class words present in the protocol's prohibited list or CM data
        classes = set()
        if getattr(self.core, "rules", None) is not None:
            classes.update(self.core.rules.prohibited_classes or [])
        for r in self.core.idx.by_domain.get("CM", [])[:2000]:
            c = normalize_class_token(r.get("CMCLAS"))
            if c:
                classes.add(c)
        for c in sorted(classes, key=len, reverse=True):
            words = c.lower().replace("_", " ")
            if words and re.search(rf"\b{re.escape(words)}s?\b", low_s):
                f["cm_class"] = c
                if it.finding_code is None:
                    it.finding_code = "PROHIBITED_MEDICATION" if c in (getattr(self.core.rules, "prohibited_classes", []) if self.core.rules else []) else None
                break
        it.filters = f

        # kind
        is_doc = bool(re.search(DOC_KEYWORDS, low_s))
        is_count = bool(re.search(COUNT_KEYWORDS, low_s))
        is_lookup = bool(re.search(LOOKUP_KEYWORDS, low_s))
        is_p360 = bool(re.search(P360_KEYWORDS, low_s))
        mentions_subjects = bool(re.search(SUBJECT_WORDS, low_s))

        if is_p360 and it.subject:
            it.kind, it.confidence = "patient360", 0.9
        elif is_doc and not (it.finding_code and (is_count or re.search(r"\bwhich\b|\bwho\b|\blist\b", low_s))):
            it.kind, it.confidence = "doc", 0.85
            it.topic = low_s
        elif is_count:
            it.kind, it.confidence = "count", 0.9
            if re.search(r"\b(?:records?|results?|events?|rows|entries|doses|medications|visits)\b", low_s) and not mentions_subjects:
                it.count_target = "records"
                if it.domains:
                    it.domain = it.domains[0]
                elif it.finding_code == "SERIOUS_AE":
                    it.domain = "AE"
                elif it.finding_code == "PROHIBITED_MEDICATION":
                    it.domain = "CM"
                elif it.finding_code == "DOSING_ERROR":
                    it.domain = "EX"
                else:
                    it.count_target = "subjects"
        elif it.subject and (is_lookup or it.domains) and not it.finding_code:
            it.kind, it.confidence = "lookup", 0.9
        elif it.finding_code:
            it.kind, it.confidence = "finding", 0.9
        elif f and (mentions_subjects or re.search(r"\bwhich\b|\bwho\b", low_s)):
            it.kind, it.confidence = "finding", 0.8   # filter-based subject list
        elif it.subject and is_lookup:
            it.kind, it.confidence = "lookup", 0.7
        elif it.subject:
            it.kind, it.confidence = "lookup", 0.5
            it.notes.append("subject id present without a clear request; returning its records")
        elif is_doc:
            it.kind, it.confidence = "doc", 0.6
            it.topic = low_s
        else:
            it.kind, it.confidence = "unknown", 0.1

        # kind hint is only a tiebreaker for low-confidence classifications
        if kind_hint and it.confidence < 0.6:
            hint = str(kind_hint).lower()
            if hint in ("count", "lookup", "finding") and it.kind == "unknown":
                it.kind, it.confidence = hint, 0.5
                it.notes.append("kind taken from question hint")
            elif hint == "trap" and it.kind == "unknown":
                it.notes.append("trap hint with unknown intent")
        return it

    # ------------------------------------------------------------- LLM merge
    def _merge_llm(self, it: Intent, text: str, llm) -> Intent:
        try:
            hint = {"codes": KNOWN_CODES, "domains": list(self.core.idx.domains)}
            data = llm.classify(text, hint)
        except Exception as exc:  # noqa: BLE001
            log.warning("llm classify failed: %s", exc)
            return it
        if not isinstance(data, dict):
            return it
        for k, v in data.items():
            if k not in ALLOWED_LLM_KEYS or v in (None, "", []):
                continue
            if k == "kind" and v in ("count", "lookup", "finding", "doc", "patient360"):
                if it.kind == "unknown":
                    it.kind, it.confidence = v, 0.6
            elif k == "finding_code" and v in KNOWN_CODES and it.finding_code is None:
                it.finding_code = v
            elif k == "site" and it.site is None:
                if str(v).upper() in text.upper():
                    it.site = self._resolve_site(str(v))
            elif k == "subject" and it.subject is None and str(v) in text:
                it.subject = str(v)
            elif k == "visit" and it.visit is None:
                it.visit = normalize_visit_code(v)
            elif k == "window_days" and it.window_days is None:
                try:
                    it.window_days = int(v)
                except (TypeError, ValueError):
                    pass
            elif k == "domains" and not it.domains and isinstance(v, list):
                it.domains = domains_from_text(" ".join(map(str, v)), self.core.idx.domains)
            elif k in ("disposition", "disposition_reason", "ae_term", "arm", "sex", "cm_class") and k not in it.filters:
                it.filters[k] = str(v).upper()
            elif k in ("ae_serious", "prohibited_med") and k not in it.filters:
                it.filters[k] = bool(v)
            elif k in ("age_min", "age_max") and k not in it.filters:
                try:
                    it.filters[k] = float(v)
                except (TypeError, ValueError):
                    pass
            elif k == "topic" and it.topic is None:
                it.topic = str(v)
        it.notes.append("intent refined by llm")
        return it

    # ----------------------------------------------------------------- answer
    def answer(self, qid: str, text: str, kind: Optional[str] = None, llm=None) -> InternalAnswer:
        t0 = time.perf_counter()
        trace = QueryTrace(cut=self.core.cut(), protocol_version=self.core.protocol_version())
        it = self.classify(text, kind_hint=kind)
        if llm is not None and it.confidence < 0.6:
            it = self._merge_llm(it, text, llm)
            trace.llm_used = True
        trace.kind = it.kind
        trace.filters = it.as_dict()
        trace.notes.extend(it.notes)

        handler = {
            "count": self._answer_count,
            "lookup": self._answer_lookup,
            "finding": self._answer_finding,
            "doc": self._answer_doc,
            "patient360": self._answer_p360,
        }.get(it.kind)
        if handler is None:
            ia = self._ambiguous(qid, it, trace, "The question could not be mapped to a study query.")
        else:
            ia = handler(qid, text, it, trace)
        ia.steps_used = max(1, len(trace.tools))
        if llm is not None and ia.status == STATUS_OK:
            ia = self._maybe_rewrite(ia, llm)
        trace.ms = (time.perf_counter() - t0) * 1000
        ia.trace = trace
        return ia

    # ----------------------------------------------------------- utilities
    def _ambiguous(self, qid: str, it: Intent, trace: QueryTrace, why: str) -> InternalAnswer:
        return InternalAnswer(
            question_id=qid, kind=it.kind if it.kind != "unknown" else "unknown", status=STATUS_AMBIGUOUS,
            answer=None, text=f"Insufficient information to determine this. {why}", evidence=[],
            confidence=confidence_for(it.kind, ambiguous=True), trace=trace,
        )

    def _rules_missing(self) -> bool:
        return getattr(self.core, "rules", None) is None

    def _site_phrase(self, site: Optional[str]) -> str:
        return f" at site {site}" if site else ""

    def _finalize_records(self, items: list[EvidenceItem], trace: QueryTrace):
        refs, res = assemble_items(self.core, items)
        trace.validation = res.summary()
        trace.evidence_selected = len(refs)
        trace.evidence_dropped = len(res.dropped)
        return refs, res

    # --------------------------------------------------------------- COUNT
    def _answer_count(self, qid: str, text: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        f = dict(it.filters)
        if it.site:
            f["site"] = it.site
        if it.visit:
            f["visit"] = it.visit
        if it.testcd:
            f["testcd"] = it.testcd
        if it.finding_code and it.finding_code not in ("SERIOUS_AE", "PROHIBITED_MEDICATION"):
            f["finding_code"] = it.finding_code
            if it.subcode:
                f["finding_subcode"] = it.subcode
        elif it.finding_code == "SERIOUS_AE" and it.subcode:
            f["finding_code"], f["finding_subcode"] = it.finding_code, it.subcode
            f.pop("ae_serious", None)
        cf = CountFilters(**{k: v for k, v in f.items() if k in CountFilters.__dataclass_fields__})
        if it.count_target == "records" and it.domain:
            trace.tools.append("count_records")
            res = TOOLS["count_records"](self.core, it.domain, **cf.__dict__)
            what = f"{it.domain} records"
        else:
            trace.tools.append("count_subjects")
            res = TOOLS["count_subjects"](self.core, **cf.__dict__)
            what = "subjects"
        trace.records_inspected = res.records_inspected
        trace.notes.extend(res.notes)
        refs, vres = self._finalize_records(res.evidence, trace)

        desc = self._describe_filters(it)
        if res.status == "insufficient":
            return InternalAnswer(
                question_id=qid, kind="count", status=STATUS_INSUFFICIENT, answer=None,
                text=f"The available data does not support a determination: {'; '.join(res.notes)}.",
                evidence=[], confidence=confidence_for("count", insufficient=True), trace=trace,
            )
        if res.value == 0:
            txt = f"0 {what}{desc}."
            if res.notes:
                txt += " " + " ".join(res.notes) + "."
            else:
                txt += " No qualifying records were found."
            conf = confidence_for("count", empty=(not refs), evidence_dropped=bool(vres.dropped),
                                  rules_missing=self._needs_rules(it) and self._rules_missing())
            return InternalAnswer(question_id=qid, kind="count", status=STATUS_OK if refs else STATUS_NO_MATCH,
                                  answer=0, text=txt, evidence=refs, confidence=conf,
                                  trace=trace, payload={"subjects": res.subjects})
        txt = f"{res.value} {what}{desc}."
        if res.collapsed:
            txt += f" {res.collapsed} duplicate enrolment(s) of the same person were counted once."
        if what == "subjects" and res.value <= MAX_LISTED_SUBJECTS:
            txt += " Subjects: " + ", ".join(res.subjects) + "."
        conf = confidence_for("count", evidence_dropped=bool(vres.dropped),
                              rules_missing=self._needs_rules(it) and self._rules_missing())
        return InternalAnswer(question_id=qid, kind="count", status=STATUS_OK, answer=int(res.value), text=txt,
                              evidence=refs, confidence=conf, trace=trace, payload={"subjects": res.subjects})

    def _needs_rules(self, it: Intent) -> bool:
        return bool(it.finding_code) or bool(it.filters.get("prohibited_med")) or it.filters.get("ae_serious") is not None

    def _describe_filters(self, it: Intent) -> str:
        parts = []
        if it.site:
            parts.append(f"at site {it.site}")
        f = it.filters
        if f.get("arm"):
            parts.append(f"in the {f['arm']} arm")
        if f.get("sex"):
            parts.append("female" if f["sex"] == "F" else "male")
        if f.get("age_min") is not None or f.get("age_max") is not None:
            lo, hi = f.get("age_min"), f.get("age_max")
            if lo is not None and hi is not None:
                parts.append(f"aged {lo:g}-{hi:g}")
            elif hi is not None:
                parts.append(f"aged {hi:g} or younger")
            else:
                parts.append(f"aged {lo:g} or older")
        if f.get("disposition"):
            s = f["disposition"].lower()
            if f.get("disposition_reason"):
                s += f" due to {f['disposition_reason'].lower()}"
            parts.append(s)
        if f.get("ae_serious"):
            parts.append("with a serious adverse event (protocol definition: AESER=Y or AESHOSP=Y)")
        if f.get("ae_term"):
            parts.append(f"with adverse event '{f['ae_term']}'")
        if f.get("prohibited_med"):
            parts.append("with a prohibited concomitant medication")
        elif f.get("cm_class"):
            parts.append(f"with medication class {f['cm_class']}")
        if it.finding_code and it.finding_code not in ("SERIOUS_AE", "PROHIBITED_MEDICATION"):
            parts.append(f"with {finding_label(it.finding_code)}")
        if it.visit:
            parts.append(f"at {it.visit}")
        if it.testcd:
            parts.append(f"for test {it.testcd}")
        return (" " + " ".join(parts)) if parts else ""

    # -------------------------------------------------------------- LOOKUP
    def _answer_lookup(self, qid: str, text: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        if not it.subject:
            return self._ambiguous(qid, it, trace, "No subject identifier was found in the question.")
        domains = it.domains or list(self.core.idx.domains)
        trace.tools.append("records_for_subject")
        res = TOOLS["records_for_subject"](
            self.core, it.subject, domains, visit=it.visit, window_days=it.window_days,
            date_from=it.date_from, date_to=it.date_to, testcd=it.testcd,
            at_visit_only=bool(it.visit and it.window_days is None),
        )
        trace.records_inspected = res.records_inspected
        trace.notes.extend(res.notes)
        if res.status == "no_subject":
            return InternalAnswer(question_id=qid, kind="lookup", status=STATUS_NO_MATCH, answer=[],
                                  text=f"No qualifying records were found: subject {it.subject} is not present in the study at cut {self.core.cut()}.",
                                  evidence=[], confidence=confidence_for("lookup", empty=True), trace=trace)
        if res.status == "insufficient":
            return InternalAnswer(question_id=qid, kind="lookup", status=STATUS_INSUFFICIENT, answer=None,
                                  text="The available data does not support a determination: " + "; ".join(res.notes) + ".",
                                  evidence=[], confidence=confidence_for("lookup", insufficient=True), trace=trace)
        refs, dropped = validate_refs(self.core, res.refs)
        refs = dedupe_sorted(refs)
        trace.validation = {"valid": len(refs), "dropped": len(dropped)}
        trace.evidence_selected = len(refs)
        scope = ", ".join(domains)
        where = ""
        if it.visit and it.window_days is not None:
            where = f" within {it.window_days} days of the {it.visit} visit (anchor {res.anchor_date}, from {res.anchor_source})"
        elif it.visit:
            where = f" at the {it.visit} visit"
        elif it.date_from or it.date_to:
            where = f" between {it.date_from or '…'} and {it.date_to or '…'}"
        if not refs:
            return InternalAnswer(question_id=qid, kind="lookup", status=STATUS_NO_MATCH, answer=[],
                                  text=f"No {scope} records for {it.subject}{where}. No qualifying records were found.",
                                  evidence=[], confidence=confidence_for("lookup", empty=True), trace=trace)
        by_dom = res.by_domain()
        dom_counts = {d: by_dom.get(d, 0) for d in (it.domains or by_dom.keys())}
        txt = f"{len(refs)} record(s) for {it.subject}{where}: " + ", ".join(f"{d} {n}" for d, n in sorted(dom_counts.items())) + "."
        if res.notes:
            txt += " " + " ".join(res.notes) + "."
        return InternalAnswer(question_id=qid, kind="lookup", status=STATUS_OK, answer=list(refs), text=txt,
                              evidence=list(refs), confidence=confidence_for("lookup", evidence_dropped=bool(dropped)),
                              trace=trace, payload={"anchor_date": str(res.anchor_date), "by_domain": by_dom})

    # ------------------------------------------------------------- FINDING
    def _answer_finding(self, qid: str, text: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        if it.finding_code:
            return self._answer_rule_finding(qid, it, trace)
        # filter-based subject list (e.g. "which subjects discontinued due to an AE")
        f = dict(it.filters)
        if it.site:
            f["site"] = it.site
        cf = CountFilters(**{k: v for k, v in f.items() if k in CountFilters.__dataclass_fields__})
        trace.tools.append("count_subjects")
        res = TOOLS["count_subjects"](self.core, **cf.__dict__)
        trace.records_inspected = res.records_inspected
        trace.notes.extend(res.notes)
        refs, vres = self._finalize_records(res.evidence, trace)
        desc = self._describe_filters(it)
        if res.status == "insufficient":
            return InternalAnswer(question_id=qid, kind="finding", status=STATUS_INSUFFICIENT, answer=None,
                                  text="The available data does not support a determination: " + "; ".join(res.notes) + ".",
                                  evidence=[], confidence=confidence_for("finding", insufficient=True), trace=trace)
        if not res.subjects:
            return InternalAnswer(question_id=qid, kind="finding", status=STATUS_NO_MATCH, answer=[],
                                  text=f"No subjects{desc}. No qualifying records were found.", evidence=[],
                                  confidence=confidence_for("finding", empty=True), trace=trace)
        subjects = [u for u in res.subjects if u in {r.usubjid for r in refs if isinstance(r, RecordKey)}] or res.subjects
        txt = f"{len(subjects)} subject(s){desc}: " + ", ".join(subjects) + "."
        return InternalAnswer(question_id=qid, kind="finding", status=STATUS_OK, answer=subjects, text=txt,
                              evidence=refs, confidence=confidence_for("finding", evidence_dropped=bool(vres.dropped)),
                              trace=trace)

    def _answer_rule_finding(self, qid: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        code = it.finding_code
        trace.tools.append("finding_subjects")
        subjects, findings, (available, why) = TOOLS["finding_subjects"](
            self.core, code, site=it.site, subcode=it.subcode, usubjid=it.subject)
        trace.records_inspected = len(findings)
        lbl = finding_label(code)
        if it.subcode:
            lbl = f"{lbl} ({it.subcode.lower().replace('_', ' ')})"
        scope = self._site_phrase(it.site) + (f" for {it.subject}" if it.subject else "")

        if not available:
            # rule could not run: distinguish from a genuine "none"
            fallback = self._filter_fallback(code)
            if fallback is not None:
                trace.notes.append(f"rule unavailable ({why}); answered from record filters")
                it2 = Intent(kind="finding", site=it.site, filters=fallback)
                return self._answer_finding(qid, "", it2, trace)
            return InternalAnswer(question_id=qid, kind="finding", status=STATUS_INSUFFICIENT, answer=None,
                                  text=f"The available data does not support a determination of {lbl}: {why}.",
                                  evidence=[], confidence=confidence_for("finding", insufficient=True,
                                                                         rules_missing=self._rules_missing()),
                                  trace=trace)

        asm = assemble(self.core, findings)
        trace.validation = asm.summary()
        trace.evidence_selected = len(asm.refs)
        trace.evidence_dropped = len(asm.validation.dropped) + len(asm.dropped_findings)
        kept_subjects = sorted({f.usubjid for f in asm.kept if f.usubjid})

        if not kept_subjects:
            elsewhere = ""
            if it.site or it.subject:
                total_subjects, _, _ = TOOLS["finding_subjects"](self.core, code, subcode=it.subcode)
                if total_subjects:
                    sites = sorted({self.core.site_of(u) or "?" for u in total_subjects})
                    elsewhere = (f" The {len(total_subjects)} subject(s) with {lbl} in this study are elsewhere "
                                 f"(site{'s' if len(sites) > 1 else ''} {', '.join(sites)}).")
            txt = f"No {lbl}{scope}.{elsewhere}" if elsewhere else f"No {lbl}{scope}. No qualifying records were found."
            return InternalAnswer(question_id=qid, kind="finding", status=STATUS_NO_MATCH, answer=[], text=txt,
                                  evidence=[], confidence=confidence_for("finding", empty=True,
                                                                         evidence_dropped=bool(asm.dropped_findings)),
                                  trace=trace)

        summaries = []
        for f in asm.kept[:6]:
            summ = f.summary or f.code
            if f.usubjid and f.usubjid not in summ:
                summ = f"{f.usubjid}: {summ}"
            summaries.append(summ.rstrip(".") + ".")
        txt = f"{len(kept_subjects)} subject(s) with {lbl}{scope}: {', '.join(kept_subjects)}. " + " ".join(summaries)
        if len(asm.kept) > 6:
            txt += f" (+{len(asm.kept) - 6} more findings.)"
        conf = confidence_for("finding", evidence_dropped=asm.evidence_dropped, flagged=asm.flagged,
                              rules_missing=self._rules_missing())
        payload = {"findings": [self._finding_payload(f) for f in asm.kept]}
        return InternalAnswer(question_id=qid, kind="finding", status=STATUS_OK, answer=kept_subjects, text=txt,
                              evidence=asm.refs, confidence=conf, trace=trace, payload=payload)

    @staticmethod
    def _filter_fallback(code: str) -> Optional[dict]:
        return {"SERIOUS_AE": {"ae_serious": True}, "PROHIBITED_MEDICATION": {"prohibited_med": True}}.get(code)

    @staticmethod
    def _finding_payload(f) -> dict:
        from backend.queries.findings import finding_summary

        return finding_summary(f)

    # ----------------------------------------------------------------- DOC
    def _answer_doc(self, qid: str, text: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        low = (it.topic or text or "").lower()
        # instruction-like sentences
        if re.search(r"instruction|automated reviewer|addressed to|tells? (?:your|the) (?:agent|reviewer)|note to|reviewers?\b", low):
            trace.tools.append("instruction_like")
            items = TOOLS["instruction_like"](self.core)
            refs = dedupe_sorted(i["ref"] for i in items)
            refs, dropped = validate_refs(self.core, refs)
            trace.evidence_selected = len(refs)
            if not items:
                return InternalAnswer(question_id=qid, kind="doc", status=STATUS_NO_MATCH, answer=[],
                                      text="No instruction-like sentences addressed to an automated reviewer were found in the study documents.",
                                      evidence=[], confidence=confidence_for("doc", empty=True), trace=trace)
            sents = []
            for i in items:
                sites = sites_mentioned(i["sentence"])
                sents.append(f"{i['document']} [{i['section']}]: \"{i['sentence']}\"" + (f" (mentions {', '.join(sites)})" if sites else ""))
            txt = (f"{len(items)} instruction-like sentence(s) found in the documents; reported as evidence only and NOT applied "
                   f"to any analysis. " + " ".join(sents))
            return InternalAnswer(question_id=qid, kind="doc", status=STATUS_OK,
                                  answer=[i["sentence"] for i in items], text=txt, evidence=list(refs),
                                  confidence=confidence_for("doc", evidence_dropped=bool(dropped)), trace=trace,
                                  payload={"instruction_like": [{k: v for k, v in i.items() if k != "ref"} for i in items]})
        # version difference
        vm = re.findall(r"\bv(?:ersion)?\s*(\d+)\b", low)
        if re.search(r"differ|change|amend|compare", low) and len(vm) >= 2:
            trace.tools.append("protocol_diff")
            a, b = int(vm[0]), int(vm[1])
            diff = TOOLS["protocol_diff"](self.core, a, b)
            ra, rb = self.core.resolver.registry.get(a), self.core.resolver.registry.get(b)
            refs = []
            for r in (ra, rb):
                if r is not None:
                    for name in diff:
                        ref = r.refs.get({"visit_window_days": "visit_window", "prohibited_classes": "prohibited",
                                          "creatinine_max": "creatinine", "creatinine_unit": "creatinine"}.get(name, name))
                        if ref:
                            refs.append(DocRefKey(ref.document, ref.section))
            refs, _ = validate_refs(self.core, dedupe_sorted(refs))
            if not diff:
                return InternalAnswer(question_id=qid, kind="doc", status=STATUS_NO_MATCH, answer=[],
                                      text=f"No rule differences were found between protocol versions {a} and {b}.",
                                      evidence=list(refs), confidence=confidence_for("doc", empty=True), trace=trace)
            txt = f"Protocol v{a} vs v{b} differ in: " + "; ".join(f"{k}: {va} -> {vb}" for k, (va, vb) in diff.items()) + "."
            return InternalAnswer(question_id=qid, kind="doc", status=STATUS_OK, answer=sorted(diff), text=txt,
                                  evidence=list(refs), confidence=confidence_for("doc"), trace=trace,
                                  payload={"diff": {k: [str(va), str(vb)] for k, (va, vb) in diff.items()}})
        if re.search(r"which (?:protocol )?version|version (?:is|in force|applies|was)|current protocol", low):
            # an explicit "cut N" in the question is resolved from cuts.csv; otherwise the built cut applies
            asked_cut = None
            m_cut = re.search(r"(?<![a-z])cut\s*(\d+)(?!\d)", low)
            if m_cut:
                asked_cut = int(m_cut.group(1))
            if asked_cut is not None:
                from backend.cuts.version_manager import protocol_version_for_cut

                pv = protocol_version_for_cut(self.core.cut_table, asked_cut)
                rules = self.core.resolver.registry.get(pv) if (pv is not None and self.core.resolver is not None) else None
                shown_cut = asked_cut
            else:
                pv = self.core.protocol_version()
                rules = self.core.rules
                shown_cut = self.core.cut()
            refs = []
            if rules is not None:
                doc = self.core.documents.get(rules.document)
                if doc is not None and "1" in doc.sections:
                    refs = [DocRefKey(rules.document, "1")]
            refs, _ = validate_refs(self.core, refs)
            trace.filters["cut"] = shown_cut
            return InternalAnswer(question_id=qid, kind="doc", status=STATUS_OK if pv is not None else STATUS_INSUFFICIENT,
                                  answer=str(pv) if pv is not None else None,
                                  text=f"Protocol version {pv} is in force at cut {shown_cut}." if pv is not None else "No protocol version could be resolved.",
                                  evidence=list(refs), confidence=confidence_for("doc") if pv is not None else 0.3, trace=trace)
        if re.search(r"unit|ukat|µkat|convert", low):
            trace.tools.append("unit_facts")
            facts = TOOLS["unit_facts"](self.core)
            refs, _ = validate_refs(self.core, dedupe_sorted(f["ref"] for f in facts))
            if not facts:
                return InternalAnswer(question_id=qid, kind="doc", status=STATUS_NO_MATCH, answer=[],
                                      text="No unit statements were found in the applicable laboratory manual.",
                                      evidence=[], confidence=confidence_for("doc", empty=True), trace=trace)
            return InternalAnswer(question_id=qid, kind="doc", status=STATUS_OK, answer=[f["sentence"] for f in facts],
                                  text="Laboratory manual unit statements: " + " ".join(f"\"{f['sentence']}\"" for f in facts),
                                  evidence=list(refs), confidence=confidence_for("doc"), trace=trace)
        trace.tools.append("protocol_rule_text")
        rt = TOOLS["protocol_rule_text"](self.core, low)
        if rt is None:
            return self._ambiguous(qid, it, trace, "No matching protocol rule topic was recognised.")
        refs, _ = validate_refs(self.core, [rt["ref"]])
        return InternalAnswer(question_id=qid, kind="doc", status=STATUS_OK, answer=str(rt["value"]),
                              text=f"{rt['document']} §{rt['section']} (version {rt['version']}): \"{rt['sentence']}\" -> {rt['rule']} = {rt['value']}.",
                              evidence=list(refs), confidence=confidence_for("doc"), trace=trace, payload=rt | {"ref": None})

    # ---------------------------------------------------------------- P360
    def _answer_p360(self, qid: str, text: str, it: Intent, trace: QueryTrace) -> InternalAnswer:
        trace.tools.append("patient360")
        p = TOOLS["patient360"](self.core, it.subject)
        if not p["found"]:
            return InternalAnswer(question_id=qid, kind="patient360", status=STATUS_NO_MATCH, answer=[],
                                  text=f"Subject {it.subject} is not present in the study at cut {self.core.cut()}.",
                                  evidence=[], confidence=confidence_for("patient360", empty=True), trace=trace)
        refs: list[RecordKey | DocRefKey] = []
        dm = self.core.dm(it.subject)
        if dm is not None:
            refs.append(dm.key)
        for t in p["evidence_refs"]:
            if t[0] == "DOC":
                refs.append(DocRefKey(t[1], t[2]))
            else:
                refs.append(RecordKey(*t))
        refs, _ = validate_refs(self.core, dedupe_sorted(refs))
        counts = {k: len(p[k]) for k in ("labs", "vitals", "ecg", "exposure", "adverse_events", "conmeds", "medical_history")}
        txt = (f"{it.subject} (site {p['site']}, protocol v{p['protocol_version']}, cut {p['cut']}): "
               + ", ".join(f"{k} {v}" for k, v in counts.items())
               + f"; disposition {p['disposition']['status'] if p['disposition'] else 'none'}; findings: "
               + (", ".join(p["findings"]) if p["findings"] else "none") + ".")
        return InternalAnswer(question_id=qid, kind="patient360", status=STATUS_OK, answer=[it.subject], text=txt,
                              evidence=list(refs), confidence=confidence_for("patient360"), trace=trace, payload=p)

    # ------------------------------------------------------------- rewrite
    def _maybe_rewrite(self, ia: InternalAnswer, llm) -> InternalAnswer:
        try:
            new_text, tokens = llm.rewrite(ia.text, {"answer": ia.answer if not isinstance(ia.answer, list) or all(isinstance(a, str) for a in ia.answer) else len(ia.answer),
                                                     "kind": ia.kind})
        except Exception as exc:  # noqa: BLE001
            log.warning("llm rewrite failed: %s", exc)
            return ia
        if not isinstance(new_text, str) or not new_text.strip():
            return ia
        ids_in_new = set(self._subject_re.findall(new_text))
        allowed = set(self._subject_re.findall(ia.text))
        if ids_in_new - allowed:
            ia.trace.notes.append("llm rewrite rejected: introduced identifiers")
            return ia
        nums_old = set(re.findall(r"\d+(?:\.\d+)?", ia.text))
        nums_new = set(re.findall(r"\d+(?:\.\d+)?", new_text))
        if nums_new - nums_old:
            ia.trace.notes.append("llm rewrite rejected: introduced numbers")
            return ia
        ia.text = new_text.strip()
        ia.tokens_used += int(tokens or 0)
        return ia
