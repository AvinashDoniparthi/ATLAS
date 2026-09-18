"""Guard: no practice-study literals in production code; starter schema untouched."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROD_DIRS = [ROOT / "backend", ROOT / "stage1"]

SUBJECT_ID = re.compile(r"042-S\d{2}-\d{3}")
SITE_TOKEN = re.compile(r"""(['"])S(0[1-9]|1[0-2])\1""")          # a bare 'S07' string literal
BARE_COUNTS = re.compile(r"(==|!=|>=|<=|<|>)\s*(241|240|12|27125)\b")
STRING_LITERAL = re.compile(r"""(['"])(?:(?!\1).)*\1""")


def _iter_sources():
    for d in PROD_DIRS:
        for p in d.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def test_no_practice_literals_in_production_code():
    offenders: list[str] = []
    for path in _iter_sources():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            code = line.split("#", 1)[0]
            if SUBJECT_ID.search(code):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: subject id literal")
            if SITE_TOKEN.search(code):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: site id literal")
            if BARE_COUNTS.search(code):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: practice count literal")
            for m in STRING_LITERAL.finditer(code):
                lit = m.group(0)[1:-1]
                if re.fullmatch(r"S(0[1-9]|1[0-2])", lit):
                    offenders.append(f"{path.relative_to(ROOT)}:{i}: site id string {lit!r}")
    assert not offenders, "hard-coded practice values:\n" + "\n".join(offenders)


def test_starter_schema_is_reconstructed_and_independent():
    text = (ROOT / "starter" / "schemas.py").read_text(encoding="utf-8")
    assert "RECONSTRUCTED_STARTER = True" in text
    assert "import backend" not in text and "from backend" not in text
    assert "from stage1" not in text


def test_only_atlas_imports_starter_schemas():
    importers = []
    for path in (ROOT / "backend").rglob("*.py"):
        if "starter.schemas" in path.read_text(encoding="utf-8"):
            importers.append(str(path.relative_to(ROOT)))
    assert importers == [], f"backend must not depend on the starter: {importers}"
