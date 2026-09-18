import pytest

from backend.normalization.units import Conversion, UnitRegistry, canonical_unit


# -------------------------------------------------------- canonical_unit --
@pytest.mark.parametrize("raw,expected", [
    ("U/L", "u/l"), (" u/l ", "u/l"), ("ukat/L", "ukat/l"), ("µkat/L", "ukat/l"), ("μkat/L", "ukat/l"),
    ("mg/dL", "mg/dl"), ("mg / dL", "mg/dl"), ("%", "%"), ("mmol/L", "mmol/l"), ("µmol/L", "umol/l"),
    ("", ""), (None, ""), ("   ", ""),
])
def test_canonical_unit(raw, expected):
    assert canonical_unit(raw) == expected


# -------------------------------------------------------------- convert --
def test_identity_conversion():
    reg = UnitRegistry()
    c = reg.convert("ALT", 40.4, "U/L", "u/l")
    assert c == Conversion(value=40.4, factor=1.0, from_unit="u/l", to_unit="u/l", source="identity")


def test_ukat_to_ul_builtin():
    reg = UnitRegistry()
    c = reg.convert("ALT", 3.995, "ukat/L", "U/L")
    assert c is not None
    assert c.value == pytest.approx(239.7)
    assert c.factor == 60.0 and c.source == "builtin"
    assert c.from_unit == "ukat/l" and c.to_unit == "u/l"


def test_micro_sign_variants_convert():
    reg = UnitRegistry()
    for u in ("µkat/L", "μkat/L", "ukat/L", "UKAT/L"):
        c = reg.convert("AST", 2.437, u, "U/L")
        assert c is not None and c.value == pytest.approx(146.22)


def test_inverse_registered():
    reg = UnitRegistry()
    c = reg.convert("ALT", 240.0, "U/L", "ukat/L")
    assert c is not None and c.value == pytest.approx(4.0) and c.factor == pytest.approx(1 / 60)


def test_generic_applies_to_any_test():
    reg = UnitRegistry()
    assert reg.convert("XYZ", 1.0, "ukat/L", "U/L").value == pytest.approx(60.0)
    assert reg.convert(None, 1.0, "ukat/L", "U/L").value == pytest.approx(60.0)


def test_test_specific_builtins():
    reg = UnitRegistry()
    assert reg.convert("BILI", 1.0, "mg/dL", "umol/L").value == pytest.approx(17.104)
    assert reg.convert("CREAT", 1.0, "mg/dL", "µmol/L").value == pytest.approx(88.42)
    assert reg.convert("GLUC", 90.0, "mg/dL", "mmol/L").value == pytest.approx(4.995, rel=1e-3)
    assert reg.convert("GLUC", 5.0, "mmol/L", "mg/dL").value == pytest.approx(90.09, rel=1e-3)


def test_test_specific_not_leaked_to_other_tests():
    reg = UnitRegistry()
    # mg/dL -> umol/L factor differs per analyte; must not apply BILI's factor to an unknown test.
    assert reg.convert("ALT", 1.0, "mg/dL", "umol/L") is None


def test_hba1c_percent_to_mmol_mol_unsupported():
    reg = UnitRegistry()
    assert reg.convert("HBA1C", 7.0, "%", "mmol/mol") is None


def test_unknown_unit_returns_none():
    reg = UnitRegistry()
    assert reg.convert("ALT", 1.0, "furlongs", "U/L") is None
    assert reg.convert("ALT", 1.0, "U/L", "mmol/L") is None
    assert reg.convert("ALT", 1.0, "", "U/L") is None
    assert reg.convert("ALT", 1.0, "U/L", None) is None
    assert reg.convert("ALT", None, "U/L", "ukat/L") is None


def test_test_specific_preferred_over_generic():
    reg = UnitRegistry(with_builtin=False)
    reg.register(None, "a", "b", 2.0, "generic")
    reg.register("T1", "a", "b", 3.0, "specific")
    assert reg.convert("T1", 1.0, "a", "b").factor == 3.0
    assert reg.convert("T2", 1.0, "a", "b").factor == 2.0
    assert reg.convert("t1", 1.0, "a", "b").source == "specific"


def test_register_ignores_bad_input():
    reg = UnitRegistry(with_builtin=False)
    reg.register(None, "a", "a", 2.0, "x")
    reg.register(None, "", "b", 2.0, "x")
    reg.register(None, "a", "b", 0, "x")
    reg.register(None, "a", "b", "notanumber", "x")
    assert reg.known_pairs() == []
    assert reg.convert(None, 1.0, "a", "b") is None


def test_empty_registry_without_builtin():
    reg = UnitRegistry(with_builtin=False)
    assert reg.convert("ALT", 1.0, "ukat/L", "U/L") is None
    assert reg.convert("ALT", 1.0, "U/L", "U/L").source == "identity"


# ------------------------------------------------------- learn_from_text --
@pytest.mark.parametrize("text", [
    "Site reports ALT and AST in µkat/L (1 µkat/L = 60 U/L).",
    "1 ukat/L equals 60 U/L",
    "note: 1 μkat/L = 60 U/L; convert before comparing",
])
def test_learn_from_text_variants(text):
    reg = UnitRegistry(with_builtin=False)
    learned = reg.learn_from_text(text, "doc:test")
    assert learned == [(None, "ukat/l", "u/l", 60.0, "doc:test")]
    c = reg.convert("ALT", 3.995, "µkat/L", "U/L")
    assert c.value == pytest.approx(239.7) and c.source == "doc:test"
    assert reg.convert("ALT", 60.0, "U/L", "ukat/L").value == pytest.approx(1.0)


def test_learn_from_text_no_statement():
    reg = UnitRegistry(with_builtin=False)
    assert reg.learn_from_text("nothing to see here", "doc:x") == []
    assert reg.learn_from_text("", "doc:x") == []
    assert reg.learn_from_text(None, "doc:x") == []


def test_learn_from_real_lab_manual(data_dir):
    text = (data_dir / "documents" / "lab-manual.md").read_text(encoding="utf-8")
    reg = UnitRegistry(with_builtin=False)
    learned = reg.learn_from_text(text, "doc:lab-manual")
    assert (None, "ukat/l", "u/l", 60.0, "doc:lab-manual") in learned
    c = reg.convert("ALT", 3.995, "ukat/L", "U/L")
    assert c is not None and c.value == pytest.approx(239.7) and c.source == "doc:lab-manual"


def test_doc_and_builtin_agree_on_real_manual(data_dir):
    text = (data_dir / "documents" / "lab-manual.md").read_text(encoding="utf-8")
    builtin = UnitRegistry(with_builtin=True)
    doc = UnitRegistry(with_builtin=False)
    doc.learn_from_text(text, "doc:lab-manual")
    for _, f, t, factor, _ in doc.known_pairs():
        b = builtin.convert(None, 1.0, f, t)
        assert b is not None and b.factor == pytest.approx(factor)


def test_known_pairs_lists_both_directions():
    reg = UnitRegistry(with_builtin=False)
    reg.register(None, "ukat/L", "U/L", 60, "builtin")
    pairs = reg.known_pairs()
    assert (None, "ukat/l", "u/l", 60.0, "builtin") in pairs
    assert any(p[1] == "u/l" and p[2] == "ukat/l" and p[3] == pytest.approx(1 / 60) for p in pairs)
