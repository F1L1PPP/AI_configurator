"""Shared brace-balanced JSON extraction for the inner-LLM planner modules.

Several planners receive JSON wrapped in prose from an inner LLM. This walks the
text tracking brace depth (ignoring braces inside string literals) and returns
the first complete ``{...}`` object, or ``None``. A regex like ``r"\\{[\\s\\S]*\\}"``
would over-grab across multiple objects or trailing braces; this stops at the
first balanced closing brace.
"""

from __future__ import annotations


def extract_first_json_object(text: str) -> str | None:
    """Return the first brace-balanced JSON object substring in ``text``, or None."""
    depth = 0
    in_string = False
    escape = False
    start = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                return text[start : i + 1]
            if depth < 0:
                # Unmatched closing brace before any opening — bail.
                return None
    return None
