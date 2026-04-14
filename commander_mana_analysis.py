"""
commander_mana_analysis.py

Simulates 1000 opening hands (7 cards + 4 drawn) and reports what percentage
of the time the lands in hand can produce enough coloured mana to cast a
target spell by the turn equal to its CMC.

Lands are the primary mana sources. Non-land cards tagged "mana" (Sol Ring,
Arcane Signet, Birds of Paradise, Faeburrow Elder, Selvala, ramp spells, etc.)
each contribute +1 pip of any deck colour toward the CMC threshold.

Usage
-----
    python commander_mana_analysis.py --commander
    python commander_mana_analysis.py --custom "{3}{W}{W}"
"""

import argparse
import random

from analyze import ALL_MANA, DECK_COLORS, _land_colors, draw_hand, load_cards, load_deck, load_tags, parse_mana_cost

COMMANDER_NAME = "Atla Palani, Nest Tender"
COMMANDER_COST = "{1}{W}{R}{G}"


def _available_pips(hand, card_data, card_tags):
    """
    Build the list of mana pips available from the hand.

    - Each land contributes one pip: the frozenset of colours it can produce.
    - Each non-land card tagged 'mana' contributes one pip of DECK_COLORS,
      representing the mana acceleration it provides (rock, dork, or ramp spell).
    """
    pips = []
    for card in hand:
        entry = card_data.get(card, {})
        type_line = entry.get('type_line', '') or ''
        if 'Land' in type_line:
            colors = _land_colors(entry)
            if colors:
                pips.append(frozenset(colors))
        elif 'mana' in card_tags.get(card, []):
            pips.append(DECK_COLORS)
    return pips


def _can_cast_from_lands(cost, hand, card_data, card_tags):
    """
    Return True if the lands in `hand` can satisfy `cost` using at most CMC
    land plays (one land per turn up to the turn equal to the spell's CMC).

    Uses greedy bipartite matching, most-restrictive pip first.
    """
    required = []
    for key, count in cost.items():
        if key == 'generic':
            required.extend([ALL_MANA] * count)
        elif isinstance(key, tuple):
            required.extend([frozenset(key)] * count)
        else:
            required.extend([frozenset({key})] * count)

    cmc = len(required)
    available = _available_pips(hand, card_data, card_tags)

    if len(available) < cmc:
        return False  # not enough mana sources by this turn

    required_sorted = sorted(required, key=lambda req: sum(1 for src in available if req & src))

    remaining = list(available)
    for req in required_sorted:
        for i, src in enumerate(remaining):
            if req & src:
                remaining.pop(i)
                break
        else:
            return False

    return True


def simulate(deck, card_data, card_tags, cost_str, trials=10000):
    cost = parse_mana_cost(cost_str)
    cmc = sum(cost.values())

    hits = 0
    for _ in range(trials):
        hand = draw_hand(deck)  # 7 cards
        rest = list(deck)
        for card in hand:
            rest.remove(card)
        hand = hand + random.sample(rest, 4)  # +4 = 11 total

        if _can_cast_from_lands(cost, hand, card_data, card_tags):
            hits += 1

    return cmc, hits / trials * 100


def main():
    parser = argparse.ArgumentParser(
        description="Simulate opening-hand mana availability for a target spell."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--commander", action="store_true",
        help=f"Check castability of {COMMANDER_NAME} ({COMMANDER_COST})",
    )
    group.add_argument(
        "--custom", metavar="MANA_SPEC",
        help='Mana cost to check, e.g. "{3}{W}{W}"',
    )
    args = parser.parse_args()

    cost_str = COMMANDER_COST if args.commander else args.custom
    cost = parse_mana_cost(cost_str)
    if cost is None:
        print(f"Error: '{cost_str}' is not a valid mana cost.")
        return

    deck = load_deck("deck.txt")
    card_data = load_cards("cards.json")
    card_tags = load_tags("tagged.json")

    cmc, pct = simulate(deck, card_data, card_tags, cost_str)

    label = COMMANDER_NAME if args.commander else cost_str
    print(f"Can cast {label} ({cost_str}, CMC {cmc}) by turn {cmc}: {pct:.1f}%")


if __name__ == "__main__":
    main()
