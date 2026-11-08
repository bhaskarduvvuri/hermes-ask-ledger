"""Strip common secret shapes from text before it is written anywhere."""

import re

MARK = "[redacted]"

_PATTERNS = [
    # PEM private key blocks (any key type)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # key=value / key: value assignments for secret-looking names (keep the name, drop the value)
    re.compile(r"(?i)\b((?:api[_-]?key|secret|token|passw(?:or)?d|pwd|auth)\s*[:=]\s*)['\"]?[^\s'\"]+['\"]?"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                 # OpenAI / Anthropic style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),            # GitHub classic tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),          # GitHub fine-grained
    re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}"),         # Slack
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),           # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),               # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                    # long hex runs
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),              # long base64 runs
]


def _sub(match: re.Match) -> str:
    # Assignment / bearer patterns keep their first group (the label).
    if match.re.groups and match.group(1):
        return match.group(1) + MARK
    return MARK


def redact(text) -> str:
    if not isinstance(text, str):
        return ""
    for pat in _PATTERNS:
        text = pat.sub(_sub, text)
    return text
