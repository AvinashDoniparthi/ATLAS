"""Ingestion: file discovery, tolerant CSV loading, response JSON lookups."""
from __future__ import annotations

from pathlib import Path

from backend.ingestion.csv_loader import (
    TABLE_FILES,
    discover_domain_files,
    load_all_domains,
    load_domain,
    load_table,
)
from backend.ingestion.json_loader import load_monitor_decisions, load_site_replies


# --------------------------------------------------------------------- files
def test_discover_domain_files_excludes_tables(data_dir: Path):
    found = discover_domain_files(data_dir)
    assert found, "no domain files discovered"
    assert all(stem.isupper() for stem in found)
    assert not (set(found) & TABLE_FILES)
    assert "DM" in found and "LB" in found
    for p in found.values():
        assert p.exists()


def test_discover_missing_data_dir(tmp_path: Path):
    assert discover_domain_files(tmp_path / "nope") == {}


def test_load_all_domains_reports(data_dir: Path):
    domains, reports = load_all_domains(data_dir)
    assert set(domains) == set(reports)
    for dom, recs in domains.items():
        assert reports[dom].rows_kept == len(recs)
        assert "USUBJID" in reports[dom].headers


# ------------------------------------------------------------- malformed rows
def test_load_domain_tolerates_malformed_rows(tmp_path: Path):
    csv_text = "\n".join(
        [
            "USUBJID,XXSEQ,VAL,cut_available,corrected_at_cut",
            "001-S01-001,1,10,1,",           # good
            ",2,11,1,",                       # missing USUBJID -> skipped
            "001-S01-001,3,12",               # short row -> padded, kept
            "001-S01-001,4,13,1,,EXTRA",      # extra column -> truncated, kept
            "001-S01-001,1,14,1,",            # duplicate (usubjid, seq)
            "001-S01-002,abc,15,1,",          # invalid seq -> kept with issue
            "",                               # blank line ignored
        ]
    )
    p = tmp_path / "XX.csv"
    p.write_text(csv_text, encoding="utf-8")
    records, report = load_domain("XX", p)

    assert report.rows_read == 6
    assert report.rows_kept == 5
    reasons = {m["reason"] for m in report.malformed}
    assert "missing USUBJID" in reasons
    assert "short_row_padded" in reasons
    assert "extra_columns_truncated" in reasons
    assert len(report.duplicates) == 1
    assert report.duplicates[0]["seq"] == 1

    by_issue = [r for r in records if "duplicate_key" in r.issues]
    assert len(by_issue) == 1
    invalid = [r for r in records if r.seq is None]
    assert len(invalid) == 1 and "missing_or_invalid_XXSEQ" in invalid[0].issues
    # raw values preserved as strings, never coerced
    assert records[0].fields["VAL"] == "10"
    assert records[0].cut_available == 1 and records[0].corrected_at_cut is None


def test_load_domain_missing_file(tmp_path: Path):
    records, report = load_domain("ZZ", tmp_path / "ZZ.csv")
    assert records == []
    assert report.missing is True


def test_load_domain_without_seq_column(tmp_path: Path):
    p = tmp_path / "DM.csv"
    p.write_text("USUBJID,AGE,cut_available\n001-S01-001,40,1\n", encoding="utf-8")
    records, report = load_domain("DM", p)
    assert len(records) == 1
    assert records[0].seq is None
    assert records[0].issues == []
    assert records[0].site == "S01"


def test_load_domain_empty_file(tmp_path: Path):
    p = tmp_path / "AE.csv"
    p.write_text("", encoding="utf-8")
    records, report = load_domain("AE", p)
    assert records == [] and report.rows_read == 0 and report.error is None


def test_load_table_missing(tmp_path: Path):
    rows, report = load_table(tmp_path / "cuts.csv")
    assert rows == [] and report.missing


# ------------------------------------------------------------- responses json
def test_site_replies_lookup(data_dir: Path):
    sr = load_site_replies(data_dir)
    assert sr.loaded and sr.replies and sr.default
    key = next(iter(sr.replies))
    dom, usubjid, seq = key.split("|")
    seq_val = int(seq) if seq else None
    reply, hit = sr.lookup(dom, usubjid, seq_val)
    assert hit is True and reply == sr.replies[key]
    miss, hit2 = sr.lookup("LB", "000-S00-000", 999999)
    assert hit2 is False and miss == sr.default
    assert key in sr.keys_for_subject(usubjid)


def test_monitor_decisions_lookup(data_dir: Path):
    md = load_monitor_decisions(data_dir)
    assert md.loaded and md.codes()
    subject_key = next(k for k in md.decisions if k.count("-") >= 2)
    code, ident = subject_key.split("|", 1)
    assert md.lookup(code, ident) == md.decisions[subject_key]
    site_key = next((k for k in md.decisions if "-" not in k.split("|", 1)[1]), None)
    if site_key:
        code, site = site_key.split("|", 1)
        assert md.lookup(code, site) == md.decisions[site_key]
    assert md.lookup("NO_SUCH_CODE", "nobody") is None


def test_responses_missing_dir(tmp_path: Path):
    sr = load_site_replies(tmp_path)
    md = load_monitor_decisions(tmp_path)
    assert sr.loaded is False and sr.lookup("LB", "x", 1) == ([], False)
    assert md.loaded is False and md.codes() == []
