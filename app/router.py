"""Difficulty-based routing: decide whether a prompt needs the cheap or the strong model."""

REASONING_KEYWORDS = [
    "why",
    "explain",
    "analyze",
    "debug",
    "prove",
    "optimize",
    "algorithm",
    "design",
    "compare",
    "refactor",
]

LONG_PROMPT_WORDS = 50


def classify(prompt: str) -> dict:
    text = " ".join(prompt.split()).lower()
    word_count = len(text.split())

    signals = []
    matched_keywords = [word for word in REASONING_KEYWORDS if word in text]
    if matched_keywords:
        signals.append("reasoning keyword(s): " + ", ".join(matched_keywords))
    if "```" in text:
        signals.append("code block")
    if word_count > LONG_PROMPT_WORDS:
        signals.append(f"length {word_count} words > {LONG_PROMPT_WORDS}")

    tier = "strong" if signals else "cheap"
    reason = "; ".join(signals) if signals else "no strong signals"
    return {"tier": tier, "word_count": word_count, "reason": reason}
