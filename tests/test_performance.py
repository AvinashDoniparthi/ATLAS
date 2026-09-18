"""Timing guards (official limit is 120 s per question; we target far below)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.graph.study_graph import StudyGraphCore

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def core(data_dir: Path) -> StudyGraphCore:
    c = StudyGraphCore(data_dir)
    c.build()
    return c


def test_build_under_20s(data_dir: Path):
    c = StudyGraphCore(data_dir)
    t0 = time.perf_counter()
    c.build()
    assert time.perf_counter() - t0 < 20


def test_repeated_count_questions_fast(core: StudyGraphCore):
    router_mod = pytest.importorskip("backend.agent.router")
    router = router_mod.Router(core)
    site = sorted(core.idx.by_site)[0]
    q = f"How many subjects at site {site} discontinued due to an adverse event?"
    t0 = time.perf_counter()
    n = 50
    for i in range(n):
        ans = router.answer(f"perf-{i}", q)
        assert ans is not None
    avg = (time.perf_counter() - t0) / n
    assert avg < 2.0, f"average {avg:.2f}s per count question"


def test_full_finding_scan_under_30s(core: StudyGraphCore):
    rules = pytest.importorskip("backend.rules")
    core.build()  # clear derived caches so the scan is real
    t0 = time.perf_counter()
    out = rules.run_all(core)
    elapsed = time.perf_counter() - t0
    assert isinstance(out, dict)
    assert elapsed < 30, f"finding scan took {elapsed:.1f}s"
    # second call is served from the build-keyed cache
    t1 = time.perf_counter()
    rules.run_all(core)
    assert time.perf_counter() - t1 < elapsed + 1.0
