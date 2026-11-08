"""Schemas for the tools the model sees."""

ASK_UPDATE = {
    "name": "ask_update",
    "description": (
        "Update the state of one of the user's tracked asks (ids like A3, shown in the "
        "[Ask Ledger] note in the user's message). Call it when you start on an ask "
        "(in_progress), when you need something from the user to continue (needs_you, "
        "with what you need as answer_line), and when you have answered it (answered, "
        "with a one-line summary of the answer). You cannot close asks; only the user can."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Ask id, e.g. 'A3'"},
            "state": {"type": "string", "enum": ["open", "in_progress", "needs_you", "answered"],
                      "description": "New state"},
            "answer_line": {"type": "string",
                            "description": "One line: the answer, or what you need from the user"},
        },
        "required": ["id", "state"],
    },
}

ASK_LIST = {
    "name": "ask_list",
    "description": (
        "List the user's unresolved asks (open, in progress, needs you) with ids, age and "
        "the user's words. Use it when the user asks what is still pending, or to find the "
        "id of an ask before calling ask_update."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "include_answered": {"type": "boolean", "description": "Also list answered asks"},
        },
        "required": [],
    },
}
