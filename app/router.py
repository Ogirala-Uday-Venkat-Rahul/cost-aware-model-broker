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

# Two length thresholds that bracket the "ambiguous" middle band. At or below
# SHORT, a prompt is trivial enough to send cheap without paying for a judge call.
# Above LONG, it's meaty enough to send strong (and the judge would have to read the
# whole thing anyway). Anything in between, with no other signal, is ambiguous and
# gets escalated to the LLM judge.
SHORT_PROMPT_WORDS = 10
LONG_PROMPT_WORDS = 40


def classify(prompt: str) -> dict:
    text = " ".join(prompt.split()).lower()
    word_count = len(text.split())

    # A reasoning keyword or a code block means the prompt is hard regardless of
    # length, so commit to strong immediately.
    matched_keywords = [word for word in REASONING_KEYWORDS if word in text]
    has_code = "```" in text
    if matched_keywords or has_code:
        parts = []
        if matched_keywords:
            parts.append("reasoning keyword(s): " + ", ".join(matched_keywords))
        if has_code:
            parts.append("code block")
        return {"tier": "strong", "word_count": word_count, "reason": "; ".join(parts)}

    # No keyword or code block, so let length decide between the three outcomes.
    if word_count <= SHORT_PROMPT_WORDS:
        return {"tier": "cheap", "word_count": word_count,
                "reason": f"no signals, short ({word_count} <= {SHORT_PROMPT_WORDS} words)"}
    if word_count > LONG_PROMPT_WORDS:
        return {"tier": "strong", "word_count": word_count,
                "reason": f"no signals but long ({word_count} > {LONG_PROMPT_WORDS} words)"}
    return {"tier": "ambiguous", "word_count": word_count,
            "reason": f"no signals, mid-length ({word_count} words) -> needs LLM judge"}
