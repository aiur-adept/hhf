"""
cut_candidate_analysis.py

For each non-land card in the deck, removes it, runs TRIALS simulated hands,
and compares every metric to the baseline (full deck).

A negative delta means the deck does WORSE on that metric without the card
→ the card is contributing to that metric.

A positive delta means the deck does BETTER without the card
→ the card may be hurting that metric (e.g. a high-CMC horse hurts castable_horse).

Output
------
  1. Baseline metrics (full deck)
  2. Per-metric: top contributors (removing them hurts the metric most)
  3. Cut candidates: cards with the lowest total absolute impact across all metrics
"""

import re
from collections import defaultdict

from analyze import load_cards, load_deck, load_tags
from hand_types_analysis import simulate

TRIALS       = 1000
DECK_PATH    = "deck.txt"
CARDS_PATH   = "cards.json"
TAGS_PATH    = "tagged.json"
TOP_N        = 8   # how many cards to show per metric
BOTTOM_N     = 15  # how many cut candidates to show

METRICS = ['ramp', 'card_draw', 'castable_horse', 'horse_fight', 'silver', 'golden']

METRIC_LABELS = {
    'ramp':           'Ramp',
    'card_draw':      'Card Draw',
    'castable_horse': 'Castable Horse',
    'horse_fight':    'Horse + Fight',
    'silver':         'Silver (Sunmare)',
    'golden':         'Golden (Sunmare+lifelink)',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_land(card_name, card_data):
    entry = card_data.get(card_name, {})
    return 'Land' in (entry.get('type_line', '') or '')


def is_basic_land(card_name, card_data):
    entry = card_data.get(card_name, {})
    type_line = entry.get('type_line', '') or ''
    return 'Basic' in type_line and 'Land' in type_line


def unique_nonland_cards(deck, card_data):
    """Return sorted list of unique non-land card names in the deck."""
    seen = set()
    result = []
    for card in deck:
        if card not in seen and not is_land(card, card_data):
            seen.add(card)
            result.append(card)
    return sorted(result)


def remove_one(deck, card_name):
    """Return a new deck list with one copy of card_name removed."""
    d = list(deck)
    d.remove(card_name)
    return d


def fmt_delta(d):
    sign = '+' if d >= 0 else ''
    return f"{sign}{d:+.1f}%"


def bar(delta, width=20):
    """ASCII bar centred at zero."""
    filled = int(abs(delta) / 100 * width * 5)
    filled = min(filled, width)
    if delta < 0:
        return ('[' + ' ' * (width - filled) + '#' * filled + '|' + ' ' * width + ']',)
    else:
        return ('[' + ' ' * width + '|' + '#' * filled + ' ' * (width - filled) + ']',)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    deck      = load_deck(DECK_PATH)
    card_data = load_cards(CARDS_PATH)
    card_tags = load_tags(TAGS_PATH)

    # --- baseline ---
    print(f"Running baseline ({TRIALS} trials)...", flush=True)
    baseline = simulate(deck, card_data, card_tags, trials=TRIALS)

    print(f"\nBaseline  ({TRIALS} trials, {len(deck)}-card deck)\n")
    for m in METRICS:
        print(f"  {METRIC_LABELS[m]:<30} {baseline[m]:5.1f}%")

    # --- per-card simulation ---
    candidates = unique_nonland_cards(deck, card_data)
    print(f"\nTesting {len(candidates)} non-land cards...\n", flush=True)

    # deltas[card][metric] = result_without_card[metric] - baseline[metric]
    deltas = {}
    for i, card in enumerate(candidates, 1):
        trimmed = remove_one(deck, card)
        result  = simulate(trimmed, card_data, card_tags, trials=TRIALS)
        deltas[card] = {m: result[m] - baseline[m] for m in METRICS}
        print(f"  [{i:2d}/{len(candidates)}] {card}", flush=True)

    # --- per-metric: top contributors (most negative delta = biggest loss when removed) ---
    print("\n" + "=" * 72)
    print("TOP CONTRIBUTORS PER METRIC")
    print("(negative delta = deck is WORSE without this card)\n")
    print("=" * 72)

    for m in METRICS:
        # sort by delta ascending (most negative = biggest contributor first)
        ranked = sorted(deltas.items(), key=lambda kv: kv[1][m])
        print(f"\n  {METRIC_LABELS[m]}")
        print(f"  {'Card':<35} {'Delta':>8}")
        print(f"  {'-'*35}  {'-'*7}")
        for card, d in ranked[:TOP_N]:
            delta = d[m]
            marker = ' <--' if delta < -3 else ''
            print(f"  {card:<35} {fmt_delta(delta):>8}{marker}")

    # --- cut candidates: lowest total absolute impact ---
    print("\n" + "=" * 72)
    print("CUT CANDIDATES  (lowest total |delta| across all metrics)")
    print("Cards that barely move any metric when removed\n")
    print("=" * 72 + "\n")

    impact = {
        card: sum(abs(d[m]) for m in METRICS)
        for card, d in deltas.items()
    }
    ranked_by_impact = sorted(impact.items(), key=lambda kv: kv[1])

    print(f"  {'Card':<35} {'Total |delta|':>14}  Per-metric breakdown")
    print(f"  {'-'*35}  {'-'*13}  {'-'*40}")
    for card, total in ranked_by_impact[:BOTTOM_N]:
        breakdown = '  '.join(
            f"{m[:4]}:{fmt_delta(deltas[card][m])}"
            for m in METRICS
            if abs(deltas[card][m]) >= 0.5   # only show non-trivial shifts
        )
        if not breakdown:
            breakdown = "(no metric moved ≥0.5%)"
        print(f"  {card:<35} {total:>10.1f}%    {breakdown}")

    # --- biggest movers overall (good for identifying key cards) ---
    print("\n" + "=" * 72)
    print("MOST IMPACTFUL CARDS  (highest total |delta| — your load-bearing pieces)\n")
    print("=" * 72 + "\n")

    print(f"  {'Card':<35} {'Total |delta|':>14}  Biggest metric shift")
    print(f"  {'-'*35}  {'-'*13}  {'-'*40}")
    for card, total in reversed(ranked_by_impact[-TOP_N:]):
        biggest_m = max(METRICS, key=lambda m: abs(deltas[card][m]))
        biggest_d = deltas[card][biggest_m]
        print(f"  {card:<35} {total:>10.1f}%    {METRIC_LABELS[biggest_m]}: {fmt_delta(biggest_d)}")


if __name__ == "__main__":
    main()
