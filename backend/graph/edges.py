"""Edge/node type vocabulary and counting for the study graph.

The graph is a dictionary graph: nodes are typed ids, edges are (src, type, dst)
tuples kept in adjacency lists. Counts feed the official build statistics.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

# node types
STUDY = "STUDY"
SITE = "SITE"
SUBJECT = "SUBJECT"
PERSON = "PERSON"
VISIT = "VISIT"
RECORD = "RECORD"
PROTOCOL = "PROTOCOL_VERSION"
REFRANGE = "REFERENCE_RANGE"
DOCUMENT = "DOCUMENT"

# edge types
HAS_SITE = "HAS_SITE"
ENROLLED = "ENROLLED"
HAS_RECORD = "HAS_RECORD"
HAS_VISIT = "HAS_VISIT"
AT_VISIT = "AT_VISIT"
COMPARED_TO = "COMPARED_TO"
UNDER_PROTOCOL = "UNDER_PROTOCOL"
POSSIBLE_DUPLICATE_OF = "POSSIBLE_DUPLICATE_OF"
SAME_PERSON = "SAME_PERSON"
GOVERNED_BY = "GOVERNED_BY"
CORRECTED_BY = "CORRECTED_BY"


def node_id(kind: str, *parts: object) -> str:
    return kind + ":" + "/".join("" if p is None else str(p) for p in parts)


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    out: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    inc: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    edge_count: int = 0
    node_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    edge_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add_node(self, nid: str, kind: str, **attrs) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {"kind": kind, **attrs}
            self.node_counts[kind] += 1
        return nid

    def add_edge(self, src: str, etype: str, dst: str) -> None:
        self.out[src].append((etype, dst))
        self.inc[dst].append((etype, src))
        self.edge_count += 1
        self.edge_counts[etype] += 1

    def neighbours(self, nid: str, etype: Optional[str] = None) -> list[str]:
        return [d for t, d in self.out.get(nid, []) if etype is None or t == etype]

    def predecessors(self, nid: str, etype: Optional[str] = None) -> list[str]:
        return [s for t, s in self.inc.get(nid, []) if etype is None or t == etype]

    def has(self, nid: str) -> bool:
        return nid in self.nodes

    def stats(self) -> dict:
        return {
            "nodes": len(self.nodes),
            "edges": self.edge_count,
            "node_types": dict(sorted(self.node_counts.items())),
            "edge_types": dict(sorted(self.edge_counts.items())),
        }


def iter_edges(g: Graph) -> Iterable[tuple[str, str, str]]:
    for src, lst in g.out.items():
        for t, dst in lst:
            yield src, t, dst
