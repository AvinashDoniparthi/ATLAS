"""
Protocol parser — extracts executable *rule parameters* from a protocol document.

Every value is read from the document text at runtime (thresholds, windows,
schedule, prohibited classes, doses). Nothing study-specific is hard-coded here.
Each extracted rule carries a ``DocRef`` (document, section, sentence) so answers
can cite the protocol as evidence. Rules that cannot be extracted are ``None``
and absent from ``refs`` — downstream code must treat that as "insufficient
protocol information", never fall back to a default.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.ingestion.document_loader import Document, Section, split_sentences

log = logging.getLogger(__name__)


@dataclass
class DocRef:
    document: str
    section: str
    sentence: str


@dataclass
class Rule:
    name: str
    value: Any
    ref: Optional[DocRef]
    ok: bool


@dataclass
class ProtocolRules:
    version: int
    document: str
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    hba1c_min: Optional[float] = None
    hba1c_max: Optional[float] = None
    inclusion_raw: list[str] = field(default_factory=list)
    exclusion_raw: list[str] = field(default_factory=list)
    hepatic_uln_multiple: Optional[float] = None
    creatinine_max: Optional[float] = None
    creatinine_unit: Optional[str] = None
    pregnancy_excluded: bool = False
    visit_schedule: dict[str, int] = field(default_factory=dict)
    visit_window_days: Optional[int] = None
    prohibited_classes: list[str] = field(default_factory=list)
    sae_hosp_flag_rule: bool = False
    sae_criteria: list[str] = field(default_factory=list)
    hys_transaminase_multiple: Optional[float] = None
    hys_bili_multiple: Optional[float] = None
    hys_window_days: Optional[int] = None
    expected_dose: dict[str, float] = field(default_factory=dict)
    dose_unit: Optional[str] = None
    refs: dict[str, DocRef] = field(default_factory=dict)

    def rule(self, name: str) -> Rule:
        """Uniform accessor: Rule(name, value, ref, ok)."""
        value = getattr(self, name, None)
        ref = self.refs.get(name)
        ok = value not in (None, {}, [], False) or name in self.refs
        return Rule(name=name, value=value, ref=ref, ok=ok)


# --------------------------------------------------------------------------- #
# Token helpers (exported)
# --------------------------------------------------------------------------- #
def normalize_class_token(s: Any) -> str:
    """'Systemic Glucocorticoid' → 'SYSTEMIC_GLUCOCORTICOID'."""
    s = "" if s is None else str(s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.strip().upper())
    return re.sub(r"_+", "_", s).strip("_")


def normalize_visit_code(s: Any) -> str:
    """Map visit labels ('Week 8', 'WEEK8', 'End of Study', 'EOS', 'Day 0') to
    canonical codes: SCREENING, BASELINE, WEEKn, EOS, DAYn (unknown → upper token)."""
    raw = "" if s is None else str(s).strip()
    low = raw.lower()
    if not low:
        return ""
    if re.search(r"\bscreen", low):
        return "SCREENING"
    if re.search(r"\bbaseline\b|\brandomi[sz]ation\b", low):
        return "BASELINE"
    if low in ("eos", "eot") or re.search(r"end\s*of\s*(study|treatment)|\bfinal\s+visit\b", low):
        return "EOS"
    m = re.search(r"\b(?:week|wk|w)\s*[-_]?\s*(\d+)\b", low)
    if m:
        return f"WEEK{int(m.group(1))}"
    m = re.search(r"\b(?:day|d)\s*_?\s*([-−–]?\s*\d+)\b", low)
    if m:
        return f"DAY{int(re.sub(r'[−–\s]', lambda x: '-' if x.group(0) != ' ' else '', m.group(1)))}"
    return normalize_class_token(raw)


def match_prohibited(cmclas: Any, cmtrt: Any, prohibited: list[str]) -> Optional[str]:
    """Return the matching prohibited entry, or None.

    Matching is token-based: the CM class (preferred) or the CM treatment name is
    normalised to tokens; a protocol entry matches when ALL of its tokens are
    present in the candidate's tokens. So protocol 'Systemic Glucocorticoid'
    matches class 'SYSTEMIC_GLUCOCORTICOID' but a bare class 'GLUCOCORTICOID' does
    NOT match it (the protocol restricts to systemic use — we do not widen the
    rule). Exact normalised equality always matches.
    """
    if not prohibited:
        return None
    for candidate in (cmclas, cmtrt):
        cand = normalize_class_token(candidate)
        if not cand:
            continue
        cand_tokens = set(cand.split("_"))
        for entry in prohibited:
            ent = normalize_class_token(entry)
            if not ent:
                continue
            if ent == cand:
                return entry
            ent_tokens = set(ent.split("_"))
            if ent_tokens and ent_tokens <= cand_tokens:
                return entry
    return None


# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #
_NUM = r"(\d+(?:[.,]\d+)?)"
_MULT = r"(?:×|x|X|\*|times)"
_PM = r"(?:±|\+/-|\+-|\+\s*/\s*-|plus\s+or\s+minus)"
_MINUS = "[-−–]"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _find_section(doc: Document, *keywords: str) -> Optional[Section]:
    for sec in doc.sections.values():
        title = sec.title.lower()
        if any(k in title for k in keywords):
            return sec
    return None


def _bullets(text: str) -> list[str]:
    return [re.sub(r"^\s*[-*•]\s*", "", ln).strip() for ln in text.splitlines()
            if re.match(r"^\s*[-*•]\s+\S", ln)]


def _sentence_with(text: str, pattern: re.Pattern) -> Optional[str]:
    for s in split_sentences(text):
        if pattern.search(s):
            return s
    for ln in text.splitlines():
        if pattern.search(ln):
            return ln.strip()
    return None


def _ref(doc: Document, sec: Optional[Section], sentence: str) -> DocRef:
    return DocRef(document=doc.name, section=sec.key if sec else "body", sentence=sentence)


def _uln_multiple(text: str) -> Optional[float]:
    m = re.search(rf">\s*{_NUM}\s*{_MULT}\s*ULN", text, re.I)
    return _num(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
def parse_protocol(doc: Document) -> ProtocolRules:
    version = doc.version if doc.version is not None else 0
    rules = ProtocolRules(version=version, document=doc.name)
    try:
        _parse_into(doc, rules)
    except Exception as exc:  # noqa: BLE001 — never let a parse error crash a build
        log.warning("protocol parse error in %s: %s", doc.name, exc)
    return rules


def _parse_into(doc: Document, r: ProtocolRules) -> None:
    whole = doc.text or ""

    # ---- inclusion ------------------------------------------------------- #
    inc = _find_section(doc, "inclusion")
    inc_text = inc.text if inc else whole
    r.inclusion_raw = _bullets(inc_text) if inc else []
    m = re.search(rf"age\s*{_NUM}\s*(?:{_MINUS}|to|and)\s*{_NUM}", inc_text, re.I)
    if m:
        r.age_min, r.age_max = _num(m.group(1)), _num(m.group(2))
        r.refs["age"] = _ref(doc, inc, m.group(0))
    else:
        log.warning("%s: age range not found", doc.name)
    m = re.search(rf"hba1c\s*(?:between|of|from)?\s*{_NUM}\s*%?\s*(?:{_MINUS}|to|and)\s*{_NUM}\s*%?", inc_text, re.I)
    if m:
        r.hba1c_min, r.hba1c_max = _num(m.group(1)), _num(m.group(2))
        r.refs["hba1c"] = _ref(doc, inc, m.group(0))
    else:
        log.warning("%s: HbA1c range not found", doc.name)

    # ---- exclusion ------------------------------------------------------- #
    exc = _find_section(doc, "exclusion")
    exc_text = exc.text if exc else ""
    r.exclusion_raw = _bullets(exc_text)
    for b in r.exclusion_raw:
        low = b.lower()
        if re.search(r"\b(alt|ast|hepatic|liver)\b", low) and r.hepatic_uln_multiple is None:
            mult = _uln_multiple(b)
            if mult is not None:
                r.hepatic_uln_multiple = mult
                r.refs["hepatic"] = _ref(doc, exc, b)
        if "creatinine" in low and r.creatinine_max is None:
            m = re.search(rf"creatinine\s*>\s*{_NUM}\s*([A-Za-zµμ/%]+)?", b, re.I)
            if m:
                r.creatinine_max = _num(m.group(1))
                r.creatinine_unit = m.group(2)
                r.refs["creatinine"] = _ref(doc, exc, b)
        if re.search(r"\bpregnan", low):
            r.pregnancy_excluded = True
            r.refs["pregnancy"] = _ref(doc, exc, b)

    # ---- visit schedule and window -------------------------------------- #
    vis = _find_section(doc, "visit", "schedule")
    vis_text = vis.text if vis else whole
    schedule = _parse_schedule(vis_text)
    if schedule:
        r.visit_schedule = schedule
        sent = _sentence_with(vis_text, re.compile(r"\bday\b", re.I)) or vis_text.strip().splitlines()[0]
        r.refs["visit_schedule"] = _ref(doc, vis, sent)
    else:
        log.warning("%s: visit schedule not found", doc.name)
    m = re.search(rf"{_PM}\s*{_NUM}\s*days?", vis_text, re.I)
    if m:
        r.visit_window_days = int(round(_num(m.group(1))))
        r.refs["visit_window"] = _ref(doc, vis, _sentence_with(vis_text, re.compile(re.escape(m.group(0)))) or m.group(0))
    else:
        log.warning("%s: visit window not found", doc.name)

    # ---- prohibited medications ----------------------------------------- #
    pro = _find_section(doc, "prohibited", "concomitant")
    if pro:
        classes = [normalize_class_token(b) for b in _bullets(pro.text)]
        r.prohibited_classes = [c for c in classes if c]
        r.refs["prohibited"] = _ref(doc, pro, pro.text.strip())
    else:
        log.warning("%s: prohibited medication section not found", doc.name)

    # ---- safety reporting / SAE ----------------------------------------- #
    saf = _find_section(doc, "safety reporting", "safety", "adverse")
    saf_text = saf.text if saf else whole
    m = re.search(r"serious adverse events?\s*\(([^)]*)\)", saf_text, re.I)
    if m:
        r.sae_criteria = [c.strip() for c in re.split(r",|;", m.group(1)) if c.strip()]
        r.refs["sae"] = _ref(doc, saf, _sentence_with(saf_text, re.compile(r"serious adverse", re.I)) or m.group(0))
    hosp = _sentence_with(saf_text, re.compile(r"AESHOSP\s*=\s*['\"]?Y", re.I))
    if hosp and re.search(r"\bserious\b", hosp, re.I):
        r.sae_hosp_flag_rule = True
        r.refs["sae_hosp"] = _ref(doc, saf, hosp)

    # ---- liver safety / Hy's law ---------------------------------------- #
    liv = _find_section(doc, "liver", "hepat", "hy")
    liv_text = liv.text if liv else whole
    hy_sent = _sentence_with(liv_text, re.compile(r"hy.s law", re.I)) or liv_text
    m = re.search(rf"(?:ALT|AST)[^.]*?>\s*{_NUM}\s*{_MULT}\s*ULN", hy_sent, re.I)
    if m:
        r.hys_transaminase_multiple = _num(m.group(1))
    m = re.search(rf"bilirubin[^.]*?>\s*{_NUM}\s*{_MULT}\s*ULN", hy_sent, re.I)
    if m:
        r.hys_bili_multiple = _num(m.group(1))
    m = re.search(rf"within\s*{_NUM}\s*days?", hy_sent, re.I)
    if m:
        r.hys_window_days = int(round(_num(m.group(1))))
    if r.hys_transaminase_multiple is not None or r.hys_bili_multiple is not None:
        r.refs["hys_law"] = _ref(doc, liv, hy_sent.strip())
    else:
        log.warning("%s: Hy's law rule not found", doc.name)

    # ---- dosing ---------------------------------------------------------- #
    dos = _find_section(doc, "dosing", "dose", "treatment")
    dos_text = dos.text if dos else whole
    dose_sent = _sentence_with(dos_text, re.compile(r"other than", re.I)) or dose_sent_fallback(dos_text)
    if dose_sent:
        doses = _parse_doses(dose_sent)
        if doses:
            r.expected_dose = {arm: mg for arm, (mg, _u) in doses.items()}
            units = {u for _mg, u in doses.values() if u}
            r.dose_unit = units.pop() if len(units) == 1 else (next(iter(units)) if units else None)
            r.refs["dosing"] = _ref(doc, dos, dose_sent.strip())
    if not r.expected_dose:
        log.warning("%s: expected doses not found", doc.name)


def dose_sent_fallback(text: str) -> Optional[str]:
    return _sentence_with(text, re.compile(rf"{_NUM}\s*mg", re.I))


_ARM_WORDS = {
    "DRUG": re.compile(r"\b(drug|active|treatment|verum)\b", re.I),
    "PLACEBO": re.compile(r"\bplacebo\b", re.I),
}


def _parse_doses(sentence: str) -> dict[str, tuple[float, Optional[str]]]:
    """'other than 10 mg (drug arm) or 0 mg (placebo)' → {'DRUG': (10.0,'mg'), 'PLACEBO': (0.0,'mg')}."""
    out: dict[str, tuple[float, Optional[str]]] = {}
    for m in re.finditer(rf"{_NUM}\s*(mg|g|mcg|µg|ug|ml|mL)\b\s*(?:\(([^)]*)\)|(?:for|in|to)\s+(?:the\s+)?([A-Za-z\- ]+?)\s+(?:arm|group))?", sentence, re.I):
        mg, unit, paren, trailing = _num(m.group(1)), m.group(2), m.group(3), m.group(4)
        label = paren or trailing or ""
        arm = None
        for token, pat in _ARM_WORDS.items():
            if pat.search(label):
                arm = token
                break
        if arm is None and label:
            arm = normalize_class_token(label.replace("arm", ""))
        if arm and arm not in out:
            out[arm] = (mg, unit)
    return out


def _parse_schedule(text: str) -> dict[str, int]:
    """Parse 'Screening (Day −14), Baseline (Day 0), Weeks 2, 4, 8 …, End of Study (Day 182)'."""
    sched: dict[str, int] = {}
    body = " ".join(text.split())
    # Explicit "(Day N)" labels
    for m in re.finditer(rf"([A-Za-z][A-Za-z ]*?)\s*\(\s*day\s*({_MINUS}?\s*\d+)\s*\)", body, re.I):
        code = normalize_visit_code(m.group(1).strip())
        day = int(re.sub(rf"{_MINUS}", "-", m.group(2)).replace(" ", ""))
        if code:
            sched[code] = day
    # "Weeks 2, 4, 8 and 12" lists
    for m in re.finditer(r"\bweeks?\s+((?:\d+\s*(?:,|and|&)?\s*)+)", body, re.I):
        for n in re.findall(r"\d+", m.group(1)):
            sched.setdefault(f"WEEK{int(n)}", int(n) * 7)
    # "Week 8 (Day 56)" handled by first loop; standalone "Day N" visits
    for m in re.finditer(r"\bday\s+(\d+)\s+visit", body, re.I):
        sched.setdefault(f"DAY{int(m.group(1))}", int(m.group(1)))
    return sched
