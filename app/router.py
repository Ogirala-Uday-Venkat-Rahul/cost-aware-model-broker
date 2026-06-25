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


def classify(prompt: str) -> str:
    text = " ".join(prompt.split()).lower()
    word_count = len(text.split())

    has_reasoning_keyword = any(word in text for word in REASONING_KEYWORDS)
    has_code = "```" in text

    if has_reasoning_keyword or has_code or word_count > LONG_PROMPT_WORDS:
        return "strong"
    return "cheap"
