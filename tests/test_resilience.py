"""The study changes on disk mid-stage: the facade must re-read without a restart."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from stage1.atlas import StudyGraph


@pytest.fixture()
def study(data_dir: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "study"
    shutil.copytree(data_dir, dst)
    return dst


def _rewrite_cuts_last_version(path: Path) -> tuple[int, int]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        headers = list(rows[0].keys())
    old = int(rows[-1]["protocol_version"])
    versions = sorted({int(r["protocol_version"]) for r in rows})
    new = versions[0] if old != versions[0] else old + 1
    rows[-1]["protocol_version"] = str(new)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    return old, new


def test_ensure_built_reacts_to_data_change(study: Path):
    g = StudyGraph(str(study))
    s0 = g.build()
    pv0, rec0, cut0 = s0["protocol_version"], s0["records"], s0["cut"]

    # (a) protocol version in force at the last cut changes
    old, new = _rewrite_cuts_last_version(study / "data" / "cuts.csv")
    assert pv0 == old
    # (b) a new LB row appears at a later cut
    lb = study / "data" / "LB.csv"
    with open(lb, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = next(reader)
        first = next(reader)
    row = dict(zip(headers, first))
    row["LBSEQ"] = "99999"
    row["cut_available"] = str(cut0 + 1)
    with open(lb, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([row[h] for h in headers])

    assert g.core.data_changed()
    g.ensure_built()
    s1 = g.stats()
    assert s1["protocol_version"] == new != pv0
    assert s1["records"] == rec0 + 1
    assert s1["cut"] == cut0 + 1
    assert g.core.record("LB", row["USUBJID"], 99999) is not None
    assert not g.core.data_changed()


def test_build_at_cut_rereads_after_change(study: Path):
    g = StudyGraph(str(study))
    cuts = g.core.cut_table or []
    s0 = g.build(cut=3)
    old, new = _rewrite_cuts_last_version(study / "data" / "cuts.csv")
    # editing the LAST cut must not change cut 3 unless cut 3 is the last listed
    s1 = g.build(cut=3)
    last_cut = max(c.cut for c in g.core.cut_table)
    if last_cut == 3:
        assert s1["protocol_version"] == new
    else:
        assert s1["protocol_version"] == s0["protocol_version"]
    assert g.core.loaded_signature != s0.get("_sig", None)  # reloaded
    s2 = g.build()
    assert s2["protocol_version"] == new
