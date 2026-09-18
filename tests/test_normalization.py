import pytest

from backend.normalization.missing import clean, is_missing
from backend.normalization.rows import normalize_row, to_int, to_upper
from backend.normalization.values import LabValue, parse_lab_value, parse_number


# ---------------------------------------------------------------- values --
@pytest.mark.parametrize("raw,expected", [("12.4", 12.4), ("12", 12.0), ("1e2", 100.0), (" 7.0 ", 7.0), ("-0.5", -0.5), (".5", 0.5), ("+3", 3.0)])
def test_numeric(raw, expected):
    lv = parse_lab_value(raw)
    assert lv.kind == "numeric" and lv.is_numeric and lv.value == expected and lv.raw == raw


def test_numeric_from_python_number():
    assert parse_lab_value(5).value == 5.0
    assert parse_lab_value(2.5).value == 2.5


def test_comma_decimal():
    lv = parse_lab_value("12,4")
    assert lv.is_numeric and lv.value == 12.4 and lv.note == "comma_decimal"
    lv = parse_lab_value("117,9")
    assert lv.value == 117.9
    lv = parse_lab_value("-0,5")
    assert lv.value == -0.5


def test_dot_decimal_unchanged():
    assert parse_lab_value("12.4").value == 12.4
    assert parse_lab_value("12.4").note is None


def test_thousands_separator_is_not_coerced():
    lv = parse_lab_value("1,234.5")
    assert lv.kind == "non_numeric" and lv.value is None
    lv = parse_lab_value("1,234,567")
    assert lv.kind == "non_numeric" and lv.value is None


@pytest.mark.parametrize("raw,op,bound", [("<5", "<", 5.0), ("< 5", "<", 5.0), (">200", ">", 200.0), ("<=1", "<=", 1.0), (">= 0,5", ">=", 0.5)])
def test_censored(raw, op, bound):
    lv = parse_lab_value(raw)
    assert lv.kind == "censored" and lv.operator == op and lv.bound == bound
    assert lv.value is None and not lv.is_numeric
    assert lv.value != 0


@pytest.mark.parametrize("raw", ["ND", "nd", "N/D", "NOT DONE", "Not Detected", "BLQ", "NA", "n/a"])
def test_not_detected(raw):
    lv = parse_lab_value(raw)
    assert lv.kind == "not_detected" and lv.value is None and not lv.is_numeric


@pytest.mark.parametrize("raw", [None, "", "   ", "\t"])
def test_missing_value(raw):
    lv = parse_lab_value(raw)
    assert lv.kind == "missing" and lv.value is None and not lv.is_numeric


@pytest.mark.parametrize("raw", ["POSITIVE", "abc", "12abc", "1.2.3", "--", True])
def test_non_numeric(raw):
    lv = parse_lab_value(raw)
    assert lv.kind == "non_numeric" and lv.value is None


def test_censored_and_nd_never_zero():
    for raw in ["<5", "ND", "", None, "BLQ"]:
        lv = parse_lab_value(raw)
        assert lv.value is None
        assert lv.value != 0.0


def test_labvalue_frozen():
    lv = parse_lab_value("1")
    with pytest.raises(Exception):
        lv.value = 2  # type: ignore[misc]


def test_parse_number():
    assert parse_number("10") == 10.0
    assert parse_number("7,5") == 7.5
    assert parse_number("<5") is None
    assert parse_number("ND") is None
    assert parse_number("") is None
    assert parse_number(None) is None
    assert parse_number("abc") is None


def test_every_lborres_in_practice_data_is_classified(data_dir):
    import csv
    from collections import Counter

    kinds = Counter()
    with open(data_dir / "data" / "LB.csv", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            lv = parse_lab_value(row.get("LBORRES"))
            kinds[lv.kind] += 1
            if lv.kind in ("censored", "not_detected", "missing"):
                assert lv.value is None
    assert kinds["non_numeric"] == 0
    assert kinds["numeric"] > 0


# --------------------------------------------------------------- missing --
@pytest.mark.parametrize("v", [None, "", "  ", "NA", "n/a", "NULL", "none", "."])
def test_is_missing_true(v):
    assert is_missing(v)


@pytest.mark.parametrize("v", ["0", 0, "x", "ND", "<5", False])
def test_is_missing_false(v):
    assert not is_missing(v)


def test_clean():
    assert clean("  abc ") == "abc"
    assert clean("") is None
    assert clean(None) is None
    assert clean("NA") is None
    assert clean(5) == "5"


# ------------------------------------------------------------------ rows --
def test_normalize_row_strips_without_coercion():
    row = normalize_row({" USUBJID ": " 042-X ", "LBORRES": " 12,4 ", "N": 3, None: "z"})
    assert row == {"USUBJID": "042-X", "LBORRES": "12,4", "N": 3, "": "z"}
    assert isinstance(row["LBORRES"], str)


def test_normalize_row_empty():
    assert normalize_row({}) == {}
    assert normalize_row(None) == {}


@pytest.mark.parametrize("v,expected", [("5", 5), ("5.0", 5), (5, 5), (" 12 ", 12), ("-3", -3)])
def test_to_int_ok(v, expected):
    assert to_int(v) == expected


@pytest.mark.parametrize("v", ["5.5", "abc", "", None, "NA", True, "nan", "inf"])
def test_to_int_none(v):
    assert to_int(v) is None


def test_to_upper():
    assert to_upper(" drug ") == "DRUG"
    assert to_upper("") is None
    assert to_upper(None) is None
