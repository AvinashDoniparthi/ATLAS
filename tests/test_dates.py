import datetime

import pytest

from backend.normalization.dates import MONTHS, ParsedDate, days_between, parse_date


def test_iso():
    p = parse_date("2026-01-15")
    assert p.ok and p.date == datetime.date(2026, 1, 15) and p.fmt == "ISO"
    assert p.raw == "2026-01-15"


def test_iso_with_whitespace():
    p = parse_date("  2026-01-15 ")
    assert p.ok and p.date == datetime.date(2026, 1, 15)


def test_iso_datetime_truncated():
    p = parse_date("2026-03-30T14:22:00Z")
    assert p.ok and p.date == datetime.date(2026, 3, 30) and p.fmt == "ISO_DATETIME"


@pytest.mark.parametrize("raw", ["28-JAN-2026", "28-jan-2026", "28-Jan-2026"])
def test_dd_mon_yyyy_case_insensitive(raw):
    p = parse_date(raw)
    assert p.ok and p.date == datetime.date(2026, 1, 28) and p.fmt == "DD-MON-YYYY"


def test_dd_mon_yyyy_unknown_month():
    p = parse_date("28-XYZ-2026")
    assert not p.ok and p.date is None and "unknown_month" in p.reason


def test_two_digit_year_is_ambiguous():
    p = parse_date("28-JAN-26")
    assert not p.ok and p.reason == "ambiguous_two_digit_year"


def test_yyyy_slash():
    p = parse_date("2026/02/03")
    assert p.ok and p.date == datetime.date(2026, 2, 3) and p.fmt == "YYYY/MM/DD"


def test_dd_slash_mm_yyyy_day_first():
    p = parse_date("03/02/2026")
    assert p.ok and p.date == datetime.date(2026, 2, 3) and p.fmt == "DD/MM/YYYY"


def test_compact():
    p = parse_date("20260215")
    assert p.ok and p.date == datetime.date(2026, 2, 15) and p.fmt == "YYYYMMDD"


def test_space_separated_month_name():
    p = parse_date("5 March 2026")
    assert p.ok and p.date == datetime.date(2026, 3, 5)


@pytest.mark.parametrize("raw", ["2026-13-45", "31-FEB-2026", "2026/00/10", "32/01/2026", "20261340"])
def test_invalid_calendar_dates_do_not_raise(raw):
    p = parse_date(raw)
    assert not p.ok and p.date is None and p.reason and p.raw == raw


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing(raw):
    p = parse_date(raw)
    assert not p.ok and p.reason == "missing" and p.date is None


@pytest.mark.parametrize("raw", ["garbage", "Jan 2026", "2026", "12-2026", "N/A"])
def test_unrecognised_does_not_raise(raw):
    p = parse_date(raw)
    assert not p.ok and p.reason == "unrecognised_format"


def test_date_objects_passthrough():
    d = datetime.date(2026, 5, 1)
    assert parse_date(d).date == d
    assert parse_date(datetime.datetime(2026, 5, 1, 10, 0)).date == d


def test_frozen():
    p = parse_date("2026-01-01")
    with pytest.raises(Exception):
        p.date = None  # type: ignore[misc]


def test_days_between_parsed_and_date():
    a = parse_date("2026-01-01")
    b = parse_date("15-JAN-2026")
    assert days_between(a, b) == 14
    assert days_between(b, a) == -14
    assert days_between(a, datetime.date(2026, 1, 3)) == 2
    assert days_between(datetime.date(2026, 1, 3), a) == -2


def test_days_between_with_bad_side_is_none():
    assert days_between(parse_date("bad"), parse_date("2026-01-01")) is None
    assert days_between(parse_date("2026-01-01"), None) is None
    assert days_between(None, None) is None


def test_months_table():
    assert MONTHS["JAN"] == 1 and MONTHS["DEC"] == 12 and len(MONTHS) == 12


def test_every_date_in_practice_data_parses(data_dir):
    """Regression: every non-empty date cell across all domain CSVs must parse."""
    import csv

    bad = []
    for path in sorted((data_dir / "data").glob("*.csv")):
        if not path.stem.isupper():
            continue
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            date_cols = [c for c in (reader.fieldnames or []) if c.endswith("DTC")]
            for row in reader:
                for c in date_cols:
                    v = row.get(c) or ""
                    if v.strip() and not parse_date(v).ok:
                        bad.append((path.stem, c, v))
    assert bad == []
