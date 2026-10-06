"""Typed JSON graph, retaining history and source provenance."""
import hashlib

from app.schemas.phase3 import GraphEdge, GraphNode, KnowledgeGraph
from app.summarization.entities import accepted_speakers, person_id


def build_graph(job_id, revision, units, topics, events, relations):
    nodes, edges = {}, []
    by_unit = {u.unit_id: u for u in units}

    def node(node_id, kind, label, data=None):
        nodes[node_id] = GraphNode(node_id=node_id, kind=kind, label=label, data=data or {})

    def edge(source, target, kind, evidence=None, derived=False):
        ident = hashlib.sha256(f"{source}|{target}|{kind}".encode()).hexdigest()[:16]
        edges.append(GraphEdge(edge_id="edge_" + ident, source=source, target=target, kind=kind, evidence=evidence or [], derived=derived))

    def evidence_node(ref):
        ident = "evidence_" + hashlib.sha256(f"{revision}|{ref.unit_id}|{ref.start_char}|{ref.end_char}".encode()).hexdigest()[:16]
        node(ident, "Evidence", ref.quote, ref.model_dump())
        return ident

    meeting = "meeting_" + job_id
    node(meeting, "Meeting", job_id, {"source_revision": revision})
    for label, speaker in accepted_speakers(units).items():
        sid = "speaker_" + label
        node(sid, "Speaker", label)
        if speaker["name"]:
            pid = "person_" + label + "_" + person_id(speaker["name"])
            node(pid, "Person", speaker["name"], {"identity_basis": "accepted_phase2_mapping"})
            edge(sid, pid, "IDENTIFIES", derived=True)
    for t in topics:
        node(t.topic_id, "Topic", t.title, t.model_dump())
        edge(meeting, t.topic_id, "HAS_TOPIC", derived=True)
    kind_names = {"claim": "Claim", "proposal": "Proposal", "decision": "Decision", "action": "ActionItem", "question": "Question", "risk": "Risk"}
    for e in events:
        node(e.event_id, kind_names[e.kind], e.description, e.model_dump())
        for ref in e.evidence:
            edge(evidence_node(ref), e.event_id, "SUPPORTS", [ref])
        for t in e.topic_ids:
            edge(e.event_id, t, "ABOUT", e.evidence, derived=True)
        speaker = by_unit[e.anchor_unit_id].speaker_id
        if speaker:
            edge(e.event_id, "speaker_" + speaker, "STATED_BY", [r for r in e.evidence if r.unit_id == e.anchor_unit_id])
        if e.validation_status != "supported":
            continue
        if e.owner_speaker_id:
            edge(e.event_id, "speaker_" + e.owner_speaker_id, "COMMITTED_BY" if e.status == "committed" else "ASSIGNED_TO",
                 [r for r in e.evidence if "owner_speaker_id" in r.supports_fields])
        if e.owner_text:
            pid = "mentioned_" + e.event_id
            node(pid, "Person", e.owner_text, {"attendance": "not_inferred"})
            edge(e.event_id, pid, "REQUESTED_OF" if e.status == "requested" else "ASSIGNED_TO",
                 [r for r in e.evidence if "owner_text" in r.supports_fields])
        if e.deadline_text:
            did = "deadline_" + e.event_id
            node(did, "Deadline", e.deadline_text, {"resolved_date": None})
            edge(e.event_id, did, "HAS_DEADLINE", [r for r in e.evidence if "deadline_text" in r.supports_fields])
    for r in relations:
        edge(r.source, r.target, r.kind, r.evidence)
    return KnowledgeGraph(nodes=list(nodes.values()), edges=list({e.edge_id: e for e in edges}.values()))

