"""Intent shortcut (pipeline layer 3): answer fixed operational intents with no LLM call.

Some requests have a known, fixed answer that needs no inference at all (a liveness
"ping", a help message). Catching these here returns standard text and skips
classification, the judge, and the model call entirely: the cheapest possible path.

Design notes:
- We match the WHOLE normalized prompt, not a substring, so "is there a shipping
  option?" never trips the "ping" intent.
- Typos/variants are handled by listing common phrasings explicitly, NOT by fuzzy
  matching. On short trigger words fuzzy matching produces false positives
  ("ping" ~ "ring"/"sing"), and a false positive means a wrong canned answer.
- A prompt we don't recognize simply returns None and falls through to normal
  routing. A missed shortcut is harmless (one cheap model call); a wrong shortcut
  is not. So we bias hard toward precision.
"""
import string

HELP_TEXT = (
    "I'm a model-routing service. Send a prompt to /complete and I'll route it to a "
    "cheap or strong model based on difficulty, then return the answer. Use /route to "
    "see the routing decision without spending a model call."
)

# Each accepted phrasing maps straight to its canned answer. Listing several keys
# that point to the same answer (e.g. the help variants -> HELP_TEXT) is how we
# absorb common phrasings and likely typos without any fuzzy logic.
CANNED_ANSWERS = {
    "ping": "pong",
    "help": HELP_TEXT,
    "halp": HELP_TEXT,
    "commands": HELP_TEXT,
    "what can you do": HELP_TEXT,
    "what do you do": HELP_TEXT,
    "how do i use this": HELP_TEXT,
    "version": "model-router v1.0",
}


def _normalize(prompt: str) -> str:
    """Lowercase, collapse whitespace, and trim surrounding punctuation.

    So "Ping!", "  ping ", and "ping" all reduce to the same key. Casing and
    trailing "?"/"!" shouldn't decide whether an intent matches.
    """
    text = " ".join(prompt.split()).lower()
    return text.strip(string.punctuation + " ")


def match_intent(prompt: str):
    """Return the canned answer for a fixed intent, or None if it isn't one."""
    return CANNED_ANSWERS.get(_normalize(prompt))
