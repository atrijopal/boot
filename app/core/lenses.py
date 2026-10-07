"""The extraction lenses (Tier 1 + Tier 2) and which LLM call extracts which lens."""

LENSES: dict[str, dict] = {
    "decision": {"prefix": "dec", "title": "Decisions", "statuses": ("confirmed", "revoked", "uncertain")},
    "action": {"prefix": "act", "title": "Action items", "statuses": ("requested", "assigned", "committed", "cancelled", "completed")},
    "question": {"prefix": "qst", "title": "Open questions", "statuses": ("open", "answered")},
    "proposal": {"prefix": "prp", "title": "Proposals considered", "statuses": ("proposed", "accepted", "rejected", "withdrawn")},
    "disagreement": {"prefix": "dis", "title": "Disagreements", "statuses": ("unresolved", "resolved")},
    "risk": {"prefix": "rsk", "title": "Risks and concerns", "statuses": ("raised",)},
    "fact": {"prefix": "fct", "title": "Key figures and facts", "statuses": ("stated",)},
    "point": {"prefix": "pnt", "title": "Discussion points", "statuses": ("discussed",)},
}
DERIVED_STATUS = {"decision": ("superseded",)}  # set only by reconciliation, never by extraction

LENS_ORDER = ["decision", "action", "question", "proposal", "disagreement", "risk", "fact", "point"]

# What each status MEANS, in plain words. Sent to the verifier with every record so it never has to guess a label.
STATUS_MEANING: dict[tuple[str, str], str] = {
    ("decision", "confirmed"): "the group explicitly agreed on or settled this (not a suggestion, a question or one person's opinion)",
    ("decision", "revoked"): "an earlier decision was explicitly cancelled",
    ("decision", "uncertain"): "it sounded like a decision but agreement is not clear",
    ("action", "requested"): "someone asked for this to be done or said it needs to be done",
    ("action", "assigned"): "another person was explicitly named as responsible for it",
    ("action", "committed"): "the speaker said they will do it themselves",
    ("action", "cancelled"): "it was called off",
    ("action", "completed"): "it was reported as already done",
    ("question", "open"): "it was asked and no real answer appears in the context (a reply such as 'I don't know' is not an answer)",
    ("question", "answered"): "it was asked and an answer that actually addresses it appears in the context ('I don't know' or a change of subject is not an answer)",
    ("proposal", "proposed"): "it was suggested but not agreed or refused in the context",
    ("proposal", "accepted"): "the context shows the group agreed to it",
    ("proposal", "rejected"): "the context shows it was turned down",
    ("proposal", "withdrawn"): "the person who suggested it took it back",
    ("disagreement", "unresolved"): "opposing positions were stated and no resolution appears in the context",
    ("disagreement", "resolved"): "opposing positions were stated and a resolution appears in the context",
    ("risk", "raised"): "someone raised it as a problem, concern, constraint or possible blocker",
    ("fact", "stated"): "this figure or fact was stated as a fact, target or constraint",
    ("point", "discussed"): "a substantive point was made in the discussion (an explanation, argument, option, reason or observation)",
}
