"""
hand_types_analysis.py

Simulates 100000 opening hands (7 cards + 5 drawn = 12 total) and reports
the percentage of hands that exhibit each of several key archetypes as
independent binary variables.

Archetypes
----------
  ramp          — at least one non-land card tagged 'mana'
  card_draw     — at least one card tagged 'card-draw' or 'pseudo-draw'
  castable_horse — at least one horse (tagged 'horses') with CMC ≤ 3 that
                   can be cast from the mana sources in the hand
                   (lands + ramp spells each count as one pip)
  horse_fight   — castable horse AND at least one card tagged 'fighting'
  silver        — Crested Sunmare is in the hand
  golden        — Crested Sunmare AND at least one card tagged 'lifelink'
"""

import random

from analyze import (
    ALL_MANA, DECK_COLORS, _land_colors,
    draw_hand, load_cards, load_deck, load_tags, parse_mana_cost,
)

import sys

TRIALS = 100000
EXTRA_DRAWS = 5        # turns of draw after the opening 7 → 12 cards total
COMMANDER_COST = "{1}{R}{G}{W}"


# ---------------------------------------------------------------------------
# Mana helpers (lands + mana-tagged non-lands, mirroring commander_mana_analysis)
# ---------------------------------------------------------------------------

def _mana_pips(hand, card_data, card_tags):
    """
    Available mana pips from a hand.
    Each land contributes one pip of its producible colours.
    Each non-land card tagged 'mana' contributes one pip of any deck colour.
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


def _is_castable(cost_str, hand, card_data, card_tags):
    """
    Return True if the mana sources in `hand` can satisfy the given cost string.
    Uses greedy bipartite pip matching, most-restrictive requirement first.
    No CMC cap — callers apply that themselves if needed.
    """
    cost = parse_mana_cost(cost_str)
    if cost is None:
        return False

    required = []
    for key, count in cost.items():
        if key == 'generic':
            required.extend([ALL_MANA] * count)
        elif isinstance(key, tuple):
            required.extend([frozenset(key)] * count)
        else:
            required.extend([frozenset({key})] * count)

    available = _mana_pips(hand, card_data, card_tags)
    if len(available) < len(required):
        return False

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


def _horse_is_castable(horse, hand, card_data, card_tags):
    """Return True if `horse` has CMC <= HORSE_CMC_CAP and is castable from hand."""
    entry = card_data.get(horse, {})
    cost_str = entry.get('mana_cost')
    if not cost_str:
        return False
    return _is_castable(cost_str, hand, card_data, card_tags)


# ---------------------------------------------------------------------------
# Hand classification
# ---------------------------------------------------------------------------

def classify_hand(hand, remaining_creature_count, card_data, card_tags):
    """
    Return a dict of bool flags, one per archetype, for a single hand.
    """
    tags_in_hand = {card: card_tags.get(card, []) for card in hand}

    def has_tag(tag):
        return any(tag in t for t in tags_in_hand.values())

    def is_land(card):
        return 'Land' in (card_data.get(card, {}).get('type_line', '') or '')

    # --- ramp ---
    ramp = any(
        'mana' in tags_in_hand[c] and not is_land(c)
        for c in hand
    )

    # --- card draw ---
    card_draw = any(
        'card-draw' in tags_in_hand[c] or 'pseudo-draw' in tags_in_hand[c]
        for c in hand
    )

    # --- castable horse ---
    castable_horses = [
        c for c in hand
        if 'horses' in tags_in_hand[c]
        and _horse_is_castable(c, hand, card_data, card_tags)
    ]
    castable_horse = bool(castable_horses)

    # --- horse fight ---
    horse_fight = castable_horse and has_tag('fighting')

    # --- silver / golden ---
    # Any castable creature tutor is treated as Crested Sunmare.
    castable_tutor = any(
        'tutor-creature' in tags_in_hand[c]
        and _is_castable(card_data.get(c, {}).get('mana_cost', ''), hand, card_data, card_tags)
        for c in hand
    )
    silver = 'Crested Sunmare' in hand or castable_tutor
    golden = silver and (has_tag('lifelink') or has_tag('lifegain')) 

    # --- bronze stretch / bronze ---
    # In a uniformly random ordering of the remaining deck, the probability
    # that Crested Sunmare is among the first K creatures = min(K, N) / N.
    # We compute the two flags jointly so that sunmare_on_top implies
    # sunmare_in_top3 (avoiding contradictory outcomes across independent rolls).
    if 'Crested Sunmare' in hand or remaining_creature_count == 0:
        sunmare_in_top3 = False
        sunmare_on_top = False
    else:
        sunmare_in_top3 = random.random() < min(3, remaining_creature_count) / remaining_creature_count
        sunmare_on_top = sunmare_in_top3 and random.random() < 1 / min(3, remaining_creature_count)

    can_cast_commander = _is_castable(COMMANDER_COST, hand, card_data, card_tags)
    bronze_stretch = can_cast_commander and sunmare_in_top3
    bronze        = can_cast_commander and sunmare_on_top

    return {
        'ramp':           ramp,
        'card_draw':      card_draw,
        'castable_horse': castable_horse,
        'horse_fight':    horse_fight,
        'silver':         silver,
        'golden':         golden,
        'bronze_stretch':  bronze_stretch,
        'bronze':          bronze,
    }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(deck, card_data, card_tags, trials=TRIALS):
    counts = {k: 0 for k in ('ramp', 'card_draw', 'castable_horse', 'horse_fight', 'bronze_stretch', 'bronze', 'silver', 'golden')}

    # Precompute total creature count in the deck once.
    deck_creature_count = sum(
        1 for c in deck
        if 'Creature' in (card_data.get(c, {}).get('type_line', '') or '')
    )

    t=0
    for _ in range(trials):
        if t % 10000 == 0:
            print(f'{t // 10000} / {trials // 10000}')
            sys.stdout.flush()
        hand = random.sample(deck, 7 + EXTRA_DRAWS)

        hand_creature_count = sum(
            1 for c in hand
            if 'Creature' in (card_data.get(c, {}).get('type_line', '') or '')
        )
        remaining_creature_count = deck_creature_count - hand_creature_count

        for key, value in classify_hand(hand, remaining_creature_count, card_data, card_tags).items():
            if value:
                counts[key] += 1

        t += 1

    return {k: v / trials * 100 for k, v in counts.items()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    deck = load_deck("deck.txt")
    card_data = load_cards("cards.json")
    card_tags = load_tags("tagged.json")

    results = simulate(deck, card_data, card_tags)

    labels = [
        ('ramp',           'Ramp piece in hand'),
        ('card_draw',      'Card draw in hand'),
        ('castable_horse', 'Castable horse'),
        ('horse_fight',    'Castable horse + fight spell'),
        ('bronze_stretch',  'Bronze stretch (cast commander + Sunmare in top 3)'),
        ('bronze',          'Bronze (cast commander + Sunmare on top)'),
        ('silver',          'Silver (Crested Sunmare)'),
        ('golden',          'Golden (Crested Sunmare + lifelink/lifegain)'),
    ]

    print(f"Hand archetype analysis -- {TRIALS} hands of {7 + EXTRA_DRAWS} cards\n")
    for key, label in labels:
        pct = results[key]
        bar = '#' * int(pct / 2)
        print(f"  {label:<52} {pct:5.1f}%  {bar}")


if __name__ == "__main__":
    main()
