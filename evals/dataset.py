"""Labeled prompts for the routing eval.

Each entry is (prompt, gold_tier), where gold_tier is the cheapest tier a human
would accept for that prompt:
  - "cheap"    a small model answers it just as well (easy / factual / short)
  - "strong"   it needs real reasoning, multi-step work, code, or nuance
  - "shortcut" a fixed operational prompt with a canned answer (no model needed)

These labels are judgment calls, on purpose: the eval measures how often the router
agrees with a reasonable human, and a few entries are deliberately the hard cases
the heuristics are known to miss, so the score is honest rather than rigged.
"""
DATASET = [
    # Fixed operational prompts -> shortcut, no model call.
    ("ping", "shortcut"),
    ("help", "shortcut"),
    ("version", "shortcut"),

    # Clearly easy -> cheap is plenty.
    ("What is the capital of France?", "cheap"),
    ("Who wrote Romeo and Juliet?", "cheap"),
    ("What is 2 + 2?", "cheap"),
    ("Translate 'good morning' to Spanish", "cheap"),
    ("What day comes after Wednesday?", "cheap"),
    ("Write a haiku about the ocean", "cheap"),

    # Clearly hard -> strong (reasoning keyword, code, or comparison).
    ("Explain why quicksort is faster than bubble sort on average", "strong"),
    ("Prove that the square root of 2 is irrational", "strong"),
    ("Design a rate limiter for a distributed API", "strong"),
    ("Refactor this loop and explain the improvement", "strong"),
    ("Compare and analyze SQL versus NoSQL for a high-write workload", "strong"),
    ("Debug this: ```for i in range(10) print(i)```", "strong"),

    # Hard but phrased plainly with no keyword -> the router's known false negative
    # (short band, sent cheap). Gold is strong; expect these to be misroutes.
    ("Is free will compatible with determinism?", "strong"),
    ("How does a transformer neural network work?", "strong"),
    ("Should I take a fixed-rate or variable-rate mortgage?", "strong"),

    # Trivial but containing a trigger word -> the router's false positive (sent
    # strong). Gold is cheap; expect these to be misroutes too.
    ("Why is the sky blue?", "cheap"),
    ("What's a good design for a birthday card?", "cheap"),

    # Mid-length, no keyword -> ambiguous band, the LLM judge decides.
    ("I'm trying to decide between renting an apartment downtown or buying a small "
     "house in the suburbs, and I can't tell which is the smarter financial move.", "strong"),
    ("My friend recommended a book last week but I forgot the title; it was about a "
     "detective in Venice solving an art theft.", "cheap"),
]
