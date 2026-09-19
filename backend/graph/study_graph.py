"""StudyGraphCore — load once, build a cut-specific graph, answer from indexes.

Pipeline: RAW (csv_loader) -> CutView (cut_manager) -> Indexes -> Graph nodes/edges
-> protocol rules for the cut (RuleResolver) -> lazily computed, build-keyed
derived findings (rules/*).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from backend.cuts.corrections import Correction, load_corrections
from backend.cuts.cut_manager import CutView, build_cut_view
from backend.cuts.version_manager import BuildKey, CutRow, data_signature, load_cut_table, protocol_version_for_cut
from backend.graph import edges as E
from backend.graph.edges import Graph, node_id
from backend.graph.indexes import Indexes
from backend.graph.nodes import Record, RecordKey, site_from_usubjid
from backend.graph.reference_ranges import NormalisedLab, ReferenceRangeIndex, load_reference_ranges, normalise_lab
from backend.ingestion.csv_loader import LoadReport, load_all_domains
from backend.ingestion.json_loader import MonitorDecisions, SiteReplies, load_monitor_decisions, load_site_replies
from backend.normalization.units import UnitRegistry

log = logging.getLogger("atlas.graph")

# DM columns used for person identity (only those present in the file are used)
PRIMARY_IDENTITY_COLS = ("DMINIT", "BRTHDTC", "SEX")
SECONDARY_IDENTITY_COLS = ("AGE", "ARM", "RFSTDTC", "SCR_HBA1C")


@dataclass
class PersonCluster:
    person_id: str
    usubjids: list[str]
    canonical: str
    matched_on: list[str]
    evidence: list[RecordKey]


@dataclass
class BuildStats:
    nodes: int = 0
    edges: int = 0
    subjects: int = 0
    unique_persons: int = 0
    duplicate_subjects: int = 0
    sites: int = 0
    records: int = 0
    build_ms: float = 0.0
    cut: Optional[int] = None
    effective_cut: Optional[int] = None
    protocol_version: Optional[int] = None
    corrections_applied: int = 0
    excluded_by_cut: int = 0
    domains: dict[str, int] = field(default_factory=dict)
    node_types: dict[str, int] = field(default_factory=dict)
    edge_types: dict[str, int] = field(default_factory=dict)
    load_reports: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    build_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "subjects": self.subjects,
            "unique_persons": self.unique_persons,
            "duplicate_subjects": self.duplicate_subjects,
            "sites": self.sites,
            "records": self.records,
            "build_ms": round(self.build_ms, 1),
            "ms": round(self.build_ms, 1),
            "cut": self.effective_cut,
            "cut_requested": self.cut,
            "protocol_version": self.protocol_version,
            "corrections_applied": self.corrections_applied,
            "records_excluded_by_cut": self.excluded_by_cut,
            "domains": self.domains,
            "node_types": self.node_types,
            "edge_types": self.edge_types,
            "load_reports": self.load_reports,
            "warnings": self.warnings,
            "build_key": self.build_key,
        }


class StudyGraphCore:
    """Everything the query layer needs, built once per (data signature, cut)."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.raw_domains: dict[str, list[Record]] = {}
        self.load_reports: dict[str, LoadReport] = {}
        self.corrections: list[Correction] = []
        self.corrections_bad: list[dict] = []
        self.cut_table: list[CutRow] = []
        self.ref_ranges: ReferenceRangeIndex = ReferenceRangeIndex()
        self.units: UnitRegistry = UnitRegistry()
        self.site_replies: SiteReplies = SiteReplies()
        self.monitor_decisions: MonitorDecisions = MonitorDecisions()
        self.resolver = None  # backend.protocol.rule_resolver.RuleResolver, set in load()
        self.documents: dict = {}
        self.loaded_signature: Optional[str] = None

        # per-build state
        self.view: Optional[CutView] = None
        self.idx: Indexes = Indexes()
        self.graph: Graph = Graph()
        self.build_key: Optional[BuildKey] = None
        self.stats: BuildStats = BuildStats()
        self.rules = None  # ProtocolRules for the cut
        self.persons: dict[str, PersonCluster] = {}
        self.person_of: dict[str, str] = {}
        self.duplicate_pairs: list[tuple[str, str]] = []
        self._lab_cache: dict[tuple, NormalisedLab] = {}
        self._derived: dict[str, Any] = {}

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        sig = data_signature(self.data_dir)
        self.raw_domains, self.load_reports = load_all_domains(self.data_dir)
        self.corrections, self.corrections_bad = load_corrections(self.data_dir)
        self.cut_table = load_cut_table(self.data_dir)
        self.ref_ranges = load_reference_ranges(self.data_dir)
        self.site_replies = load_site_replies(self.data_dir)
        self.monitor_decisions = load_monitor_decisions(self.data_dir)
        self.units = UnitRegistry()
        try:
            from backend.protocol.rule_resolver import RuleResolver

            self.resolver = RuleResolver(self.data_dir)
            self.documents = self.resolver.all_documents
            for doc in self.documents.values():
                if getattr(doc, "kind", "") == "lab_manual":
                    self.units.learn_from_text(doc.text, f"doc:{doc.name}")
        except Exception as exc:  # noqa: BLE001 - protocol layer optional at load time
            log.error("protocol layer unavailable: %s", exc)
            self.resolver = None
        self.loaded_signature = sig

    def data_changed(self) -> bool:
        return self.loaded_signature != data_signature(self.data_dir)

    # ----------------------------------------------------------------- build
    def build(self, cut: Optional[int] = None, protocol_version: Optional[int] = None) -> BuildStats:
        t0 = time.perf_counter()
        if self.loaded_signature is None or self.data_changed():
            self.load()
        self._lab_cache.clear()
        self._derived.clear()
        self.view = build_cut_view(self.raw_domains, self.corrections, cut)
        if protocol_version is not None:
            pv = protocol_version
        else:
            pv = protocol_version_for_cut(self.cut_table, self.view.effective_cut if cut is None else cut)
        self.rules = None
        if self.resolver is not None:
            if pv is not None and hasattr(self.resolver, "registry"):
                self.rules = self.resolver.registry.get(pv)
            if self.rules is None:
                self.rules = self.resolver.rules_for_cut(cut)
        self.build_key = BuildKey(self.loaded_signature or "", cut, pv, self.view.corrections_applied)

        self.idx = Indexes()
        for domain, recs in self.view.domains.items():
            headers = self.load_reports[domain].headers if domain in self.load_reports else []
            self.idx.add_domain(domain, recs, headers)
        self.idx.finalize()
        self._cluster_persons()
        self._build_graph(pv)

        st = BuildStats(
            nodes=len(self.graph.nodes),
            edges=self.graph.edge_count,
            subjects=len(self.idx.subjects),
            unique_persons=len(self.persons),
            duplicate_subjects=len(self.idx.subjects) - len(self.persons),
            sites=len(self.idx.by_site),
            records=self.view.record_count,
            cut=cut,
            effective_cut=self.view.effective_cut,
            protocol_version=pv,
            corrections_applied=self.view.corrections_applied,
            excluded_by_cut=sum(self.view.excluded_by_cut.values()),
            domains={d: len(r) for d, r in self.view.domains.items()},
            node_types=dict(self.graph.node_counts),
            edge_types=dict(self.graph.edge_counts),
            load_reports={d: r.summary() for d, r in self.load_reports.items()},
            build_key=self.build_key.short(),
        )
        if self.rules is None:
            st.warnings.append("no protocol rules resolved for this cut")
        if not self.ref_ranges.loaded:
            st.warnings.append("reference_ranges.csv missing")
        if self.idx.unparseable_dates:
            st.warnings.append(f"{self.idx.unparseable_dates} unparseable dates")
        for d, rep in self.load_reports.items():
            if rep.malformed:
                st.warnings.append(f"{d}: {len(rep.malformed)} malformed rows skipped")
        st.build_ms = (time.perf_counter() - t0) * 1000
        self.stats = st
        log.info("built graph %s in %.0f ms", self.build_key.short(), st.build_ms)
        return st

    # --------------------------------------------------------- person identity
    def _cluster_persons(self) -> None:
        self.persons = {}
        self.person_of = {}
        self.duplicate_pairs = []
        dm = self.idx.by_domain.get("DM", [])
        headers = self.load_reports["DM"].headers if "DM" in self.load_reports else []
        primary = [c for c in PRIMARY_IDENTITY_COLS if c in headers]
        secondary = [c for c in SECONDARY_IDENTITY_COLS if c in headers]
        groups: dict[tuple, list[Record]] = defaultdict(list)
        if primary:
            for r in dm:
                key = tuple((r.get(c) or "").upper() for c in primary)
                if all(key):
                    groups[key].append(r)
        seen_subjects: set[str] = set()
        for key, recs in groups.items():
            if len(recs) < 2:
                continue
            # refine by secondary columns: need >= 2 (or all present if fewer) agreeing
            recs = sorted(recs, key=lambda r: (r.cut_available if r.cut_available is not None else 0, r.usubjid))
            base = recs[0]
            cluster = [base]
            matched = list(primary)
            for other in recs[1:]:
                agree = [c for c in secondary if (base.get(c) or "").upper() == (other.get(c) or "").upper()]
                need = min(2, len(secondary))
                if len(agree) >= need:
                    cluster.append(other)
                    matched = list(primary) + agree
            if len(cluster) < 2:
                continue
            usubjids = sorted(r.usubjid for r in cluster)
            canonical = cluster[0].usubjid
            pid = f"P:{canonical}"
            self.persons[pid] = PersonCluster(pid, usubjids, canonical, matched, [r.key for r in cluster])
            for u in usubjids:
                self.person_of[u] = pid
                seen_subjects.add(u)
            for u in usubjids:
                if u != canonical:
                    self.duplicate_pairs.append((canonical, u))
        for u in self.idx.subjects:
            if u not in seen_subjects:
                pid = f"P:{u}"
                self.persons[pid] = PersonCluster(pid, [u], u, [], [])
                self.person_of[u] = pid

    # ------------------------------------------------------------- graph build
    def _build_graph(self, pv: Optional[int]) -> None:
        g = Graph()
        study = node_id(E.STUDY, self.data_dir.name)
        g.add_node(study, E.STUDY)
        proto_node = None
        if pv is not None:
            proto_node = g.add_node(node_id(E.PROTOCOL, pv), E.PROTOCOL, version=pv)
            g.add_edge(study, E.GOVERNED_BY, proto_node)
        for name in self.documents:
            g.add_node(node_id(E.DOCUMENT, name), E.DOCUMENT)
        rr_nodes: dict[tuple, str] = {}
        for rr in self.ref_ranges.ranges:
            nid = g.add_node(node_id(E.REFRANGE, rr.testcd, rr.lab, rr.unit), E.REFRANGE)
            rr_nodes[(rr.testcd, rr.lab.upper(), rr.unit)] = nid
        for site in sorted(self.idx.by_site):
            sn = g.add_node(node_id(E.SITE, site), E.SITE)
            g.add_edge(study, E.HAS_SITE, sn)
        for pid, pc in self.persons.items():
            if len(pc.usubjids) > 1:
                g.add_node(node_id(E.PERSON, pc.canonical), E.PERSON)
        for u in self.idx.subjects:
            sn_id = g.add_node(node_id(E.SUBJECT, u), E.SUBJECT)
            site = self.idx.site_of(u)
            if site:
                g.add_edge(node_id(E.SITE, site), E.ENROLLED, sn_id)
            if proto_node:
                g.add_edge(sn_id, E.UNDER_PROTOCOL, proto_node)
            pc = self.persons.get(self.person_of.get(u, ""))
            if pc and len(pc.usubjids) > 1:
                g.add_edge(node_id(E.PERSON, pc.canonical), E.SAME_PERSON, sn_id)
            for domain, recs in self.idx.by_subject[u].items():
                for r in recs:
                    rid = g.add_node(node_id(E.RECORD, domain, u, r.seq), E.RECORD, domain=domain)
                    g.add_edge(sn_id, E.HAS_RECORD, rid)
                    v = (r.get("VISIT") or "").upper().replace(" ", "")
                    if v:
                        vid = node_id(E.VISIT, u, v)
                        if not g.has(vid):
                            g.add_node(vid, E.VISIT)
                            g.add_edge(sn_id, E.HAS_VISIT, vid)
                        g.add_edge(vid, E.AT_VISIT, rid)
                    if domain == "LB":
                        nl = self.lab(r)
                        if nl.range is not None:
                            key = (nl.range.testcd, nl.range.lab.upper(), nl.range.unit)
                            if key in rr_nodes:
                                g.add_edge(rid, E.COMPARED_TO, rr_nodes[key])
        for a, b in self.duplicate_pairs:
            g.add_edge(node_id(E.SUBJECT, a), E.POSSIBLE_DUPLICATE_OF, node_id(E.SUBJECT, b))
            g.add_edge(node_id(E.SUBJECT, b), E.POSSIBLE_DUPLICATE_OF, node_id(E.SUBJECT, a))
        self.graph = g

    # --------------------------------------------------------------- helpers
    def lab(self, record: Record) -> NormalisedLab:
        """Unit-aware, range-aware normalisation of one LB record (cached per build)."""
        key = record.key.as_tuple()
        nl = self._lab_cache.get(key)
        if nl is None:
            nl = normalise_lab(record, self.idx.site_of(record.usubjid) or record.site, self.ref_ranges, self.units)
            self._lab_cache[key] = nl
        return nl

    def derived(self, name: str, compute: Callable[[], Any]) -> Any:
        """Build-keyed cache for derived findings; cleared on every build()."""
        if name not in self._derived:
            self._derived[name] = compute()
        return self._derived[name]

    def record(self, domain: str, usubjid: str, seq: Optional[int]) -> Optional[Record]:
        return self.idx.get(domain, usubjid, seq)

    def exists(self, key: RecordKey) -> bool:
        return key.as_tuple() in self.idx.by_ref

    def subjects(self) -> list[str]:
        return list(self.idx.subjects)

    def site_of(self, usubjid: str) -> Optional[str]:
        return self.idx.site_of(usubjid) or site_from_usubjid(usubjid)

    def dm(self, usubjid: str) -> Optional[Record]:
        recs = self.idx.records(usubjid, "DM")
        return recs[0] if recs else None

    def protocol_version(self) -> Optional[int]:
        return self.build_key.protocol_version if self.build_key else None

    def cut(self) -> Optional[int]:
        return self.view.effective_cut if self.view else None

    def canonical_subjects(self) -> list[str]:
        """One USUBJID per unique person (duplicates collapsed to the canonical id)."""
        return sorted(pc.canonical for pc in self.persons.values())

    def is_duplicate_enrolment(self, usubjid: str) -> bool:
        pc = self.persons.get(self.person_of.get(usubjid, ""))
        return bool(pc and len(pc.usubjids) > 1 and pc.canonical != usubjid)
