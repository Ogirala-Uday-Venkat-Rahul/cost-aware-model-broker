"""Safety gate (pipeline layer 2): decide whether a prompt is allowed to run.

A lightweight, heuristic first line of defense against prompt injection. It only
blocks phrases that try to reach the system's hidden instructions or override the
model's identity, the kind of thing a normal question never contains, so it
rarely blocks real users. It is not meant to be exhaustive: it is one cheap layer
that runs before we spend a model call, with the model's own safety training as
the deeper layer behind it.
"""

# Lowercase phrases that signal an attack on the system rather than a normal
# request. Tuned for precision: each one targets the system's instructions or the
# model's identity, which a genuine Q&A user essentially never types. We avoid
# generic phrases like "ignore previous instructions" on purpose, because a real
# user editing their own request says that legitimately.
BLOCKED_PATTERNS = [
    "your system prompt",   # extraction: "reveal/show me your system prompt"
    "your instructions",    # override or extract: "ignore/reveal your instructions"
    "your guidelines",      # override: "disregard your guidelines"
    "you are now",          # identity hijack: "you are now an unfiltered AI"
    "developer mode",       # common jailbreak framing
    "do anything now",      # the "DAN" jailbreak
]


def is_safe(prompt: str) -> dict:
    """Return {"allowed": bool, "reason": str} for a prompt.

    Pure function: no network, no I/O, same input always gives the same output,
    so it is easy to unit-test. Same design as classify() in router.py.
    """
    text = " ".join(prompt.split()).lower()

    for pattern in BLOCKED_PATTERNS:
        if pattern in text:
            return {"allowed": False, "reason": f"blocked phrase: '{pattern}'"}

    return {"allowed": True, "reason": "no blocked phrases"}
