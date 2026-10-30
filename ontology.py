"""The ontology, declared in one place: object types, link types, actions, functions.

The app reads table names and keys from here.
"""

OBJECT_TYPES = {
    "Request": {
        "table": "requests",
        "primary_key": "unique_key",
        "title": "request_text",
        "editable_properties": ["assigned_agency", "reassign_note"],
    },
    "Agency": {
        "table": "agencies",
        "primary_key": "agency",
        "title": "agency_name",
        "editable_properties": [],
    },
    "Neighborhood": {
        "table": "neighborhoods",
        "primary_key": "incident_zip",
        "title": "incident_zip",
        "editable_properties": [],
    },
}

LINK_TYPES = [
    {"name": "Request -> Agency", "from": "Request", "to": "Agency",
     "cardinality": "many-to-one", "join": {"Request": "agency", "Agency": "agency"}},
    {"name": "Request -> Neighborhood", "from": "Request", "to": "Neighborhood",
     "cardinality": "many-to-one", "join": {"Request": "incident_zip", "Neighborhood": "incident_zip"}},
]

ACTION_TYPES = {
    "reassign_request": {
        "edits": "Request",
        "parameters": {"unique_key": "string (Request primary key)",
                       "new_agency": "string (must exist as an Agency)",
                       "note": "string (optional)"},
        "writes": ["assigned_agency", "reassign_note"],
        "side_effect": "appends a row to audit_log",
    }
}

FUNCTIONS = {
    "summarize_neighborhood": {
        "input": "Neighborhood (ZIP)",
        "modes": {"raw": "LLM sees up to 200 raw open requests as 'type: descriptor' (naive baseline)",
                  "raw_types": "LLM sees up to 200 complaint types, one per line (fairer baseline)",
                  "counts": "open requests are counted in SQL first, LLM only writes the summary"},
        "output": "string (5 lines)",
    }
}
