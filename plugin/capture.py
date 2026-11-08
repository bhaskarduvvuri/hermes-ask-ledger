"""Decide which turns are the user's asks, and get their text."""

import os
import re

# Platforms Hermes uses for turns no person typed: the scheduler and delegate_task children.
NON_USER_PLATFORMS = {"cron", "subagent"}
# Gateway platforms whose inbound messages come from machines (they reach post_gateway_admission).
MACHINE_PLATFORMS = {"webhook", "msgraph_webhook"}
# Hermes marks synthetic user-role turns: background-process and automation wake events end with
# an "[INTERNAL NOTIFICATION — ...]" footer, and system notices start with "[SYSTEM". Matched as
# text only; nothing from Hermes core is imported.
_INTERNAL_MARK = "[INTERNAL NOTIFICATION"
_SYSTEM_PREFIX = "[SYSTEM"


def looks_machine_written(text: str) -> bool:
    return _INTERNAL_MARK in text or text.lstrip().startswith(_SYSTEM_PREFIX)


def extract_text(user_message) -> str:
    """Text of a turn: the string itself, or the text parts of a multimodal list."""
    if isinstance(user_message, str):
        return user_message.strip()
    if isinstance(user_message, list):
        parts = []
        for part in user_message:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                if part["text"].strip():
                    parts.append(part["text"].strip())
        return "\n".join(parts)
    return ""


def is_user_turn(*, platform="", parent_session_id="", user_message=None, **_ignored) -> bool:
    if (platform or "").lower() in NON_USER_PLATFORMS:
        return False
    if parent_session_id:
        return False
    if os.environ.get("HERMES_KANBAN_TASK"):
        return False
    text = extract_text(user_message)
    if not text or text.startswith("/") or looks_machine_written(text):
        return False
    return True


def one_line(text, limit: int) -> str:
    """Collapse whitespace to single spaces and cut to ``limit`` chars with an ellipsis."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text
