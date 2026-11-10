"""Strip the courtesy around an Ask request before the direct answers read it.

"hey kilix, could you tell me what time it is please?" asks the same thing
as "what time is it". Only the outer wrapper is removed: a greeting, the
name, please, "can you", "do you know", and a trailing "please", "thanks",
"right now" or "for me". A trailing name needs its comma, so "what is
kilix" keeps its last word. The words that say what is wanted are left alone,
and Needle is still given the request exactly as it was typed.
"""

from __future__ import annotations

import re
import sys

_TRAIL = re.compile(r"[?!.\s]+$")
_LEAD = re.compile(
    r"^(?:"
    r"(?:hey|hi|hello|ok|okay|yo)\s+kilix\b[,:]?"
    r"|kilix\s*[,:]"
    r"|(?:hey|hi|hello|ok|okay|so|um|uh|well)\s*,"
    r"|please\b,?"
    r"|quick question\s*[,:-]?"
    r"|(?:can|could|would|will) you (?:please |kindly )?(?=\S)"
    r"|do you (?:happen to )?know\b"
    r"|(?:tell me|let me know)\b(?= (?:what|how|when|where|which|who|whether|if)\b)"
    r"|i (?:want|need|would like|wanna)(?: to know)?\b(?= (?:what|how|when|where|which|who|whether|if|the|my)\b)"
    r"|i'd like to know\b"
    r"|i(?: was|'m| am)? wonder(?:ing)?\b"
    r"|check (?=(?:if|whether)\b)"
    r")\s*",
    re.IGNORECASE,
)
_TAIL = re.compile(
    r"(?:,?\s+(?:please|thanks|thank you|for me|right now|at the moment|currently)|,\s*kilix)$",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    """The request with quotes straightened, spaces collapsed, and courtesy removed."""
    text = " ".join(text.replace("’", "'").replace("‘", "'")
                    .replace("“", '"').replace("”", '"').split())
    text = _TRAIL.sub("", text).strip()
    stripped = False
    for _ in range(6):
        shorter = _TAIL.sub("", _LEAD.sub("", text, count=1), count=1).strip(" ,")
        shorter = _TRAIL.sub("", shorter).strip()
        if shorter == text or not shorter:
            break
        text = shorter
        stripped = True
    if stripped:
        text = _direct(text)
    return text


def _direct(text: str) -> str:
    """Put "what time it is" back in question order. Only after a wrapper was removed."""
    match = re.fullmatch(
        r"(what time|what day|what year|what month|what date|how late|how hot|how cold|how warm)"
        r" it is(.*)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)} is it{match.group(2)}"
    match = re.fullmatch(
        r"(?:if|whether) (there|i|[\w.' -]{1,40}?) (is|are|was|were|am) (.+)", text, re.IGNORECASE)
    if match:
        return f"{match.group(2)} {match.group(1)} {match.group(3)}"
    match = re.fullmatch(
        r"(what|who|where|how much|how many|how big|how fast|how long) (.+) (is|are)",
        text, re.IGNORECASE)
    if match and not re.search(r"\b(?:is|are|do|does|did|can|will)\b", match.group(2), re.I):
        return f"{match.group(1)} {match.group(3)} {match.group(2)}"
    return text


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    print(clean(" ".join(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
