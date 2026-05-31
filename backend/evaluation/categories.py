"""CUAD clause category -> natural-language question.

CUAD labels each contract against 41 clause categories. ``master_clauses.csv``
stores, for every category, the verbatim text span(s) a lawyer highlighted as
the answer. We turn a category into a question a user would actually type, and
treat the highlighted span(s) as the passage retrieval *should* surface.

Only a curated, high-signal subset is used. Categories that are pure metadata
(e.g. "Document Name") or that are almost always empty add noise without
testing retrieval, so they are intentionally left out. ``dataset.py`` further
filters this map to the columns that actually exist in the local CSV, so an
unexpected CUAD revision degrades gracefully instead of crashing.
"""

from __future__ import annotations

# Keys MUST match the column headers in CUAD_v1/master_clauses.csv exactly.
QUESTION_TEMPLATES: dict[str, str] = {
    "Parties": "Who are the contracting parties to this agreement?",
    "Agreement Date": "What is the date of this agreement?",
    "Effective Date": "What is the effective date of this agreement?",
    "Expiration Date": "When does this agreement expire or terminate?",
    "Renewal Term": "What is the renewal term of this agreement?",
    "Notice Period To Terminate Renewal": (
        "What notice period is required to terminate the renewal?"
    ),
    "Governing Law": "Which jurisdiction's law governs this agreement?",
    "Most Favored Nation": "Does this agreement include a most favored nation clause?",
    "Non-Compete": "Does this agreement impose a non-compete restriction?",
    "Exclusivity": "Does this agreement grant any exclusivity?",
    "No-Solicit Of Customers": (
        "Does this agreement restrict solicitation of customers?"
    ),
    "No-Solicit Of Employees": (
        "Does this agreement restrict solicitation of employees?"
    ),
    "Non-Disparagement": "Is there a non-disparagement obligation in this agreement?",
    "Termination For Convenience": (
        "Can either party terminate this agreement for convenience?"
    ),
    "Rofr/Rofo/Rofn": (
        "Does this agreement grant a right of first refusal, offer, or negotiation?"
    ),
    "Change Of Control": (
        "Are there any change of control provisions in this agreement?"
    ),
    "Anti-Assignment": (
        "Does this agreement restrict assignment to a third party?"
    ),
    "Revenue/Profit Sharing": "Does this agreement include revenue or profit sharing?",
    "Ip Ownership Assignment": (
        "How does this agreement assign intellectual property ownership?"
    ),
    "License Grant": "What license does this agreement grant?",
}
