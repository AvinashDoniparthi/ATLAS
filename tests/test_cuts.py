"""Cuts, corrections and protocol-version resolution."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.cuts.corrections import corrections_in_force, load_corrections
from backend.cuts.cut_manager import build_cut_view, max_cut_available
from backend.cuts.version_manager import (
    CutRow,
    data_signature,
    load_cut_table,
    protocol_version_for_cut,
)
from backend.ingestion.csv_loader import load_all_domains


@pytest.fixture(scope="module")
def raw(data_dir: Path):
    domains, _ = load_all_domains(data_dir)
    corrections, bad = load_corrections(data_dir)
    return domains, corrections, bad


# ------------------------------------------------------------ cut table
def test_load_cut_table_sorted(data_dir: Path):
    cuts = load_cut_table(data_dir)
    assert cuts
    assert [c.cut for c in cuts] == sorted(c.cut for c in cuts)
    assert all(c.protocol_version is not None for c in cuts)


def test_protocol_version_for_cut_edges():
    table = [CutRow(2, 1, None, None), CutRow(5, 2, None, None), CutRow(9, 3, None, None)]
    assert protocol_version_for_cut(table, 1) == 1      # below first listed
    assert protocol_version_for_cut(table, 2) == 1      # exact
    assert protocol_version_for_cut(table, 7) == 2      # between
    assert protocol_version_for_cut(table, 9) == 3      # exact last
    assert protocol_version_for_cut(table, 50) == 3     # above last
    assert protocol_version_for_cut(table, None) == 3   # None -> latest
    assert protocol_version_for_cut([], 3) is None      # empty table


def test_protocol_version_for_cut_real(data_dir: Path):
    cuts = load_cut_table(data_dir)
    versions = [c.protocol_version for c in cuts]
    assert protocol_version_for_cut(cuts, None) == versions[-1]
    assert protocol_version_for_cut(cuts, cuts[0].cut) == versions[0]


# ------------------------------------------------------------ cut views
def test_cut_view_monotone(raw):
    domains, corrections, _ = raw
    top = max_cut_available(domains)
    assert top is not None
    prev = -1
    for cut in range(1, top + 1):
        view = build_cut_view(domains, corrections, cut)
        assert view.effective_cut == cut
        assert view.record_count >= prev
        prev = view.record_count
        for domain, recs in view.domains.items():
            assert all(r.cut_available is None or r.cut_available <= cut for r in recs)
            assert len(recs) + view.excluded_by_cut[domain] == len(domains[domain])
    full = build_cut_view(domains, corrections, None)
    assert full.effective_cut == top
    assert full.record_count == sum(len(v) for v in domains.values())


def test_correction_applied_only_from_its_cut(raw):
    domains, corrections, bad = raw
    assert corrections, "practice data has corrections"
    c = corrections[0]
    before = build_cut_view(domains, corrections, c.cut - 1)
    after = build_cut_view(domains, corrections, c.cut)

    def find(view):
        for r in view.domains.get(c.domain, []):
            if r.usubjid == c.usubjid and r.seq == c.seq:
                return r
        return None

    rb, ra = find(before), find(after)
    if rb is not None:  # record may itself not be available before the correction cut
        assert rb.get(c.field) == c.old_value
        assert rb.corrections == []
    assert ra is not None
    assert ra.get(c.field) == c.new_value
    assert ra.raw(c.field) == c.old_value            # raw preserved
    assert ra.corrections[0]["cut"] == c.cut
    # raw dataset untouched
    raw_rec = next(r for r in domains[c.domain] if r.usubjid == c.usubjid and r.seq == c.seq)
    assert raw_rec.corrected_fields == {} and raw_rec.fields[c.field] == c.old_value
    assert before.corrections_pending == sum(1 for x in corrections if x.cut > c.cut - 1)
    assert after.corrections_applied >= 1


def test_corrections_in_force_ordering():
    from backend.cuts.corrections import Correction

    cs = [
        Correction(5, "LB", "u", 1, "LBORRES", "1", "2", "", 2),
        Correction(3, "LB", "u", 1, "LBORRES", "0", "1", "", 3),
        Correction(9, "LB", "u", 1, "LBORRES", "2", "3", "", 4),
    ]
    grouped = corrections_in_force(cs, 5)
    assert [c.cut for c in grouped[("LB", "u", 1)]] == [3, 5]
    assert corrections_in_force(cs, None)[("LB", "u", 1)][-1].cut == 9
    assert corrections_in_force(cs, 1) == {}


def test_later_correction_supersedes(raw):
    domains, _, _ = raw
    from backend.cuts.corrections import Correction

    rec = domains["LB"][0]
    cs = [
        Correction(1, "LB", rec.usubjid, rec.seq, "LBORRES", rec.fields["LBORRES"], "111", "a", 1),
        Correction(2, "LB", rec.usubjid, rec.seq, "LBORRES", "111", "222", "b", 2),
    ]
    view = build_cut_view(domains, cs, None)
    r = next(x for x in view.domains["LB"] if x.key == rec.key)
    assert r.get("LBORRES") == "222" and len(r.corrections) == 2


# ------------------------------------------------------------ signature
def test_data_signature_changes_on_touch(data_dir: Path, tmp_path: Path):
    dst = tmp_path / "study"
    shutil.copytree(data_dir, dst)
    s1 = data_signature(dst)
    assert s1 == data_signature(dst)
    with open(dst / "data" / "cuts.csv", "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert data_signature(dst) != s1
