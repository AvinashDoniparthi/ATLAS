"""
Document loader — reads the study documents (protocols, lab manuals, SAP) as EVIDENCE.

Documents are split into citable sections. Sentences that address an automated
reviewer/agent or issue operational commands are *detected and recorded* in
``Document.instruction_like`` so they can be reported as facts. Nothing in this
module (or anywhere in the backend) executes them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Section:
    key: str            # e.g. "7" or "units"
    number: Optional[str]
    title: str
    text: str
    heading: str


@dataclass
class InstructionLike:
    document: str
    section: str
    sentence: str
    reason: str


@dataclass
class Document:
    name: str                    # file stem, e.g. "protocol_v1", "lab-manual"
    path: str
    kind: str                    # "protocol" | "lab_manual" | "sap" | "other"
    version: Optional[int]
    text: str
    sections: dict[str, Section] = field(default_factory=dict)
    instruction_like: list[InstructionLike] = field(default_factory=list)

    def section_for(self, needle: str) -> Optional[Section]:
        """Find a section by number, key/slug, or case-insensitive substring of its title."""
        if needle is None:
            return None
        needle_s = str(needle).strip()
        if needle_s in self.sections:
            return self.sections[needle_s]
        low = needle_s.lower()
        for sec in self.sections.values():
            if sec.number == needle_s or sec.key.lower() == low:
                return sec
        slug = _slug(needle_s)
        for sec in self.sections.values():
            if sec.key == slug or _slug(sec.title) == slug:
                return sec
        for sec in self.sections.values():
            if low and low in sec.title.lower():
                return sec
        return None


# --------------------------------------------------------------------------- #
# Instruction-like sentence detection (recall oriented)
# --------------------------------------------------------------------------- #
_INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pat, re.IGNORECASE))
    for name, pat in [
        ("addresses automated reviewer", r"automated\s+reviewer"),
        ("addresses automated agent/system", r"automated\s+(agent|system|tool)s?"),
        ("note addressed to reviewers", r"\bnote\s+to\b"),
        ("reviewer directive", r"\breviewers?\s+(must|should|shall|are\s+to)\b"),
        ("directive: do not flag", r"\bdo\s+not\s+flag\b"),
        ("directive: exclusion", r"\bshould\s+be\s+excluded\b"),
        ("directive: exclude from", r"\bexclude\b.*\bfrom\b"),
        ("directive: ignore", r"\bignore\b"),
        ("directive: restart", r"\brestart\b"),
        ("directive: accept values", r"\baccept\s+the\s+values?\b"),
        ("directive: must not be treated", r"\bmust\s+not\s+be\s+treated\b"),
        ("directive: override", r"\boverride\b"),
        ("directive: disregard", r"\bdisregard\b"),
    ]
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        para = " ".join(para.split())
        if not para:
            continue
        for s in _SENTENCE_SPLIT.split(para):
            s = s.strip()
            if s:
                out.append(s)
    return out


def detect_instruction_like(text: str, document: str, section: str) -> list[InstructionLike]:
    """Return every sentence in ``text`` that looks like an instruction aimed at an
    automated reviewer or an operational command. These are facts to report, never
    rules to apply."""
    found: list[InstructionLike] = []
    for sentence in split_sentences(text):
        hits = [name for name, pat in _INSTRUCTION_PATTERNS if pat.search(sentence)]
        if hits:
            found.append(InstructionLike(document=document, section=section,
                                         sentence=sentence, reason="; ".join(hits)))
    return found


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s or "section"


_HEADING = re.compile(r"^\s*##\s+(?:(\d+(?:\.\d+)*)[.)]?\s+)?(.+?)\s*$")
_TITLE = re.compile(r"^\s*#\s+(.+?)\s*$")


def _classify(name: str, text: str) -> tuple[str, Optional[int]]:
    low = name.lower()
    head = (text or "")[:400].lower()
    if low.startswith("sap") or "statistical analysis plan" in head:
        kind = "sap"
    elif ("lab" in low and "manual" in low) or "laboratory manual" in head:
        kind = "lab_manual"
    elif "protocol" in low or "study protocol" in head:
        kind = "protocol"
    else:
        kind = "other"
    version: Optional[int] = None
    m = re.search(r"_v(\d+)$", low) or re.search(r"[-_ ]v(\d+)\b", low)
    if m:
        version = int(m.group(1))
    else:
        m = re.search(r"\bversion\s+(\d+)\b", head) or re.search(r"\bv(\d+)\b", head)
        if m:
            version = int(m.group(1))
    return kind, version


_LAB_PARA_KEYS: list[tuple[str, re.Pattern]] = [
    ("addendum", re.compile(r"\baddendum\b", re.I)),
    ("reviewer-note", re.compile(r"automated\s+reviewer|\bnote\s+to\b", re.I)),
    ("non-numeric", re.compile(r"<\s*5|\bND\b|\bblank\b|below[- ]detection|not[- ]done", re.I)),
    ("units", re.compile(r"\bunits?\b|µkat|μkat|ukat|\b(?:reports?|reported)\b[^.]*\bin\b", re.I)),
    ("reference-ranges", re.compile(r"reference[- ]ranges?", re.I)),
    ("reissued-results", re.compile(r"re-?issue|supersede", re.I)),
]


def _lab_para_key(para: str, index: int, used: set[str]) -> str:
    for key, pat in _LAB_PARA_KEYS:
        if pat.search(para):
            base = key
            break
    else:
        base = f"para-{index}"
    key = base
    n = 2
    while key in used:
        key = f"{base}-{n}"
        n += 1
    return key


def parse_sections(name: str, kind: str, text: str) -> dict[str, Section]:
    """Split markdown into sections keyed by number (``## 7. Liver safety`` → "7")
    or title slug. Documents without ``##`` headings get a "body" section plus
    paragraph-level pseudo-sections."""
    sections: dict[str, Section] = {}
    lines = (text or "").splitlines()
    current: Optional[Section] = None
    buf: list[str] = []
    saw_heading = False

    def flush():
        if current is not None:
            current.text = "\n".join(buf).strip()
            sections[current.key] = current

    for line in lines:
        m = _HEADING.match(line)
        if m:
            saw_heading = True
            flush()
            number, title = m.group(1), m.group(2).strip()
            key = number if number else _slug(title)
            if key in sections:
                key = f"{key}-{len(sections)}"
            current = Section(key=key, number=number, title=title, text="", heading=line.strip())
            buf = []
        else:
            if current is not None:
                buf.append(line)
    flush()

    if not saw_heading:
        body_lines = [ln for ln in lines if not _TITLE.match(ln)]
        body = "\n".join(body_lines).strip()
        sections["body"] = Section(key="body", number=None, title=name, text=body, heading=f"# {name}")
        paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
        used: set[str] = set(sections)
        for i, para in enumerate(paras, 1):
            if kind == "lab_manual":
                key = _lab_para_key(para, i, used)
            else:
                key = f"para-{i}"
            used.add(key)
            sections[key] = Section(key=key, number=None, title=key, text=para, heading=key)
    return sections


def load_document(path: Path) -> Optional[Document]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read document %s: %s", path, exc)
        return None
    name = Path(path).stem
    kind, version = _classify(name, text)
    sections = parse_sections(name, kind, text)
    instr: list[InstructionLike] = []
    if sections:
        for sec in sections.values():
            if sec.key == "body":
                continue  # paragraphs are covered individually
            instr.extend(detect_instruction_like(sec.text, name, sec.key))
        if all(s.key == "body" for s in sections.values()):
            instr.extend(detect_instruction_like(sections["body"].text, name, "body"))
    else:
        instr.extend(detect_instruction_like(text, name, "body"))
    return Document(name=name, path=str(path), kind=kind, version=version, text=text,
                    sections=sections, instruction_like=instr)


def load_documents(data_dir: str | Path) -> dict[str, Document]:
    """Load every ``*.md`` under ``<data_dir>/documents``. Missing folder → {}."""
    docs: dict[str, Document] = {}
    try:
        folder = Path(data_dir) / "documents"
        if not folder.is_dir():
            log.warning("documents folder not found: %s", folder)
            return docs
        for path in sorted(folder.glob("*.md")):
            doc = load_document(path)
            if doc is not None:
                docs[doc.name] = doc
    except Exception as exc:  # noqa: BLE001
        log.warning("document loading failed: %s", exc)
    return docs
