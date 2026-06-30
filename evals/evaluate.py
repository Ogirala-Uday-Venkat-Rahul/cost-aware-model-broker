"""Routing eval: how often does the router agree with hand-labeled tiers, and how
much does routing avoid the expensive model?

Run from the repo root:
    python -m evals.evaluate

Needs GROQ_API_KEY in .env, since ambiguous prompts hit the cheap model as a judge.
"""
from collections import Counter

from app.intents import match_intent
from app.router import classify
from app.provider import classify_with_llm
from evals.dataset import DATASET

# Assumed price ratio: how many times more a "strong" token costs than a "cheap"
# one. This is a stated assumption, not a quoted price -- real ratios vary by
# provider and change over time. The modeled-savings line scales with it; the
# "avoided the strong model" line below makes no price assumption at all.
PRICE_RATIO = 10


def route(prompt: str) -> str:
    """The final routed tier, mirroring /complete minus the orthogonal safety gate:
    intent shortcut first, then the difficulty heuristics, then the judge if the
    heuristics land in the ambiguous band.
    """
    if match_intent(prompt) is not None:
        return "shortcut"
    tier = classify(prompt)["tier"]
    if tier == "ambiguous":
        tier = classify_with_llm(prompt)
    return tier


def main():
    rows = [(prompt, gold, route(prompt)) for prompt, gold in DATASET]
    n = len(rows)
    dist = Counter(routed for _, _, routed in rows)
    correct = [r for r in rows if r[1] == r[2]]
    misses = [r for r in rows if r[1] != r[2]]

    print(f"Cost-Aware Model Broker -- routing eval ({n} prompts)\n")

    print("Routing distribution:")
    for tier in ("cheap", "strong", "shortcut"):
        c = dist.get(tier, 0)
        print(f"  {tier:<9}: {c:>2}  ({c / n:.0%})")

    print(f"\nAgreement with labels: {len(correct)}/{n}  ({len(correct) / n:.0%})")

    if misses:
        print("\nMisroutes (the honest part):")
        for prompt, gold, routed in misses:
            # "under" = sent cheaper than the label wanted; "over" = sent stronger
            # than needed (still correct answer, just more expensive).
            kind = "under" if gold == "strong" and routed != "strong" else "over"
            short = prompt if len(prompt) <= 60 else prompt[:57] + "..."
            print(f"  [{kind:<5}] routed {routed:<8} expected {gold:<8} | {short}")

    # No price assumption: just how many requests skipped the expensive model.
    avoided = sum(1 for _, _, routed in rows if routed in ("cheap", "shortcut"))
    print(f"\nAvoided the strong model: {avoided}/{n}  ({avoided / n:.0%})")

    # Modeled spend vs sending everything to strong. Treat each non-shortcut request
    # as ~equal token cost (a stated simplification); shortcuts cost nothing.
    routed_cost = dist.get("cheap", 0) * 1 + dist.get("strong", 0) * PRICE_RATIO
    baseline_cost = n * PRICE_RATIO
    pct = routed_cost / baseline_cost
    print(f"Modeled spend vs all-strong (strong = {PRICE_RATIO}x cheap/token): "
          f"{pct:.0%} of baseline ({1 - pct:.0%} saved)")


if __name__ == "__main__":
    main()
