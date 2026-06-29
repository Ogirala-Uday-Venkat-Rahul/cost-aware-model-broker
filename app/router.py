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

# Two length knobs that bracket the "ambiguous" middle band:
#   <= SHORT  -> confidently cheap (trivial), don't waste a judge call
#   >  LONG   -> confidently strong (meaty), and the judge would have to read it all anyway
#   in between, with no other signal -> ambiguous -> escalate to the LLM judge
SHORT_PROMPT_WORDS = 10
LONG_PROMPT_WORDS = 40


def classify(prompt: str) -> dict:
    text = " ".join(prompt.split()).lower()
    word_count = len(text.split())

    # --- Definitive signals: a reasoning keyword or a code block means the
    # prompt is hard regardless of length, so we commit to strong immediately. ---
    matched_keywords = [word for word in REASONING_KEYWORDS if word in text]
    has_code = "```" in text
    if matched_keywords or has_code:
        parts = []
        if matched_keywords:
            parts.append("reasoning keyword(s): " + ", ".join(matched_keywords))
        if has_code:
            parts.append("code block")
        return {"tier": "strong", "word_count": word_count, "reason": "; ".join(parts)}

    # --- No definitive signal: let length decide, three ways. ---
    if word_count <= SHORT_PROMPT_WORDS:
        return {"tier": "cheap", "word_count": word_count,
                "reason": f"no signals, short ({word_count} <= {SHORT_PROMPT_WORDS} words)"}
    if word_count > LONG_PROMPT_WORDS:
        return {"tier": "strong", "word_count": word_count,
                "reason": f"no signals but long ({word_count} > {LONG_PROMPT_WORDS} words)"}
    return {"tier": "ambiguous", "word_count": word_count,
            "reason": f"no signals, mid-length ({word_count} words) -> needs LLM judge"}
