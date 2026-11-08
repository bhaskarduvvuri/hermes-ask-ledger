"""A cheap, deterministic topic guess for grouping asks on the page."""

import re

# First matching row wins, so put specific topics before broad ones.
KEYWORDS = [
    ("deploy", ("deploy", "release", "rollout", "ship it", "staging", "production", "prod ")),
    ("bug", ("bug", "error", "crash", "broken", "traceback", "exception", "fails", "failing")),
    ("email", ("email", "e-mail", "inbox", "reply to", "draft a message")),
    ("schedule", ("meeting", "calendar", "schedule", "book ", "remind", "appointment")),
    ("money", ("invoice", "budget", "price", "cost", "pay ", "payment", "expense", "tax")),
    ("docs", ("docs", "document", "readme", "write-up", "writeup", "spec", "summar")),
    ("research", ("research", "paper", "compare", "find out", "look up", "investigate")),
    ("data", ("data", "csv", "spreadsheet", "sql", "query", "chart", "dataset")),
    ("code", ("code", "function", "refactor", "test", "script", "repo", "pull request")),
    ("travel", ("flight", "hotel", "trip", "train", "travel")),
]

_STOP = set("""a an the and or but if then to of in on at for with from by about into over
this that these those it its is are was were be been am do does did can could would should will
please pls me my you your we our us i hey hi hello thanks thank just also some any what which who
how why when where there here up out get make let need want""".split())


def guess_topic(text) -> str:
    low = f" {(text or '').lower()} "
    for topic, words in KEYWORDS:
        if any(w in low for w in words):
            return topic
    # Fallback: the longest non-stopword, which is usually the subject noun.
    words = [w for w in re.findall(r"[a-z][a-z-]{2,}", low) if w not in _STOP]
    if not words:
        return "general"
    return max(words, key=len)[:20]
