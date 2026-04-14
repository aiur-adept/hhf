"""
sunmare_clock.py

Simulates TRIALS games turn-by-turn (up to MAX_TURN) and reports when the
Crested Sunmare engine first comes online: Sunmare on the battlefield AND
at least one lifelink/lifegain enabler also on the battlefield.

Mana model
----------
  Available mana at turn T = the T most colour-flexible mana sources among
  the cards seen so far (one land drop per turn; ramp pieces are included
  in the pool and sorted by flexibility alongside lands).  Capping at T
  prevents impossible early assemblies (e.g. Sunmare "castable" on turn 1
  because the opening hand happened to contain five lands).

Tutor model
-----------
  A creature tutor (Eladamri's Call etc.) fetches Sunmare to hand the turn
  it resolves.  Sunmare must then be cast on a subsequent turn with {3}{W}{W}
  worth of mana.  The engine cannot fire on the same turn a tutor resolves
  unless {3}{W}{W} is also independently castable that turn.

Enabler breakdown
-----------------
  Credit goes to the cheapest (lowest CMC) castable lifelink/lifegain source
  present when the engine assembles, breaking ties in favour of lifelink over
  lifegain.  This reflects the card that was most likely already in play.

Outputs
-------
  - Per-turn and cumulative engine assembly rate
  - Enabler breakdown
  - Sunmare source split (natural draw vs creature tutor)
"""

import random
import sys
from collections import defaultdict

from analyze import (
    ALL_MANA, DECK_COLORS, _land_colors,
    load_cards, load_deck, load_tags, parse_mana_cost,
)

TRIALS   = 100_000
MAX_TURN = 10
SUNMARE_NAME  = "Crested Sunmare"
LIFELINK_TAGS   = frozenset({'lifelink', 'lifegain'})
TUTOR_TAG       = 'tutor-creature'

# Extra cards drawn beyond the baseline (7 opening hand + 1/turn) by turn T,
# measured from 100,000-game goldfish simulation in card_draw_simulator.py.
# These replace the old "count draw pieces × 1" heuristic with empirical data.
#
#   Cumul. seen:  8.0  9.1 10.3 11.6 13.1 14.9 16.9 19.1 21.6 24.3
#   Baseline:     8    9   10   11   12   13   14   15   16   17
#   Extra:        0    0    0    1    1    2    3    4    6    7
#
#   Index = turn (1-based); index 0 unused.
_DRAW_EXTRAS = [0,  0, 0, 0, 1, 1, 2, 3, 4, 6, 7]


# ---------------------------------------------------------------------------
# Mana helpers
# ---------------------------------------------------------------------------

def _all_pips(cards, card_data, card_tags):
    """Return every mana pip produced by mana sources in `cards` (uncapped)."""
    pips = []
    for card in cards:
        entry    = card_data.get(card, {})
        type_line = entry.get('type_line', '') or ''
        if 'Land' in type_line:
            colors = _land_colors(entry)
            if colors:
                pips.append(frozenset(colors))
        elif 'mana' in card_tags.get(card, []):
            pips.append(DECK_COLORS)
    return pips


def _pips_at_turn(cards, turn, card_data, card_tags):
    """
    Return the `turn` most colour-flexible mana pips from `cards`.
    Sorting descending by pip-set size keeps multi-colour sources (most
    flexible) available and uses mono-colour ones last, giving the
    bipartite matcher the best chance of satisfying colour requirements.
    """
    all_p = _all_pips(cards, card_data, card_tags)
    all_p.sort(key=lambda s: -len(s))
    return all_p[:turn]


def _satisfies(cost_str, pips):
    """True if `pips` can satisfy the mana cost described by `cost_str`."""
    if not cost_str:
        return False
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

    if len(pips) < len(required):
        return False

    required.sort(key=lambda req: sum(1 for src in pips if req & src))
    remaining = list(pips)
    for req in required:
        for i, src in enumerate(remaining):
            if req & src:
                remaining.pop(i)
                break
        else:
            return False
    return True


# ---------------------------------------------------------------------------
# Engine-component checks
# ---------------------------------------------------------------------------

def _sunmare_castable(pips, card_data):
    """True if `pips` can cast Crested Sunmare ({3}{W}{W})."""
    return _satisfies(card_data.get(SUNMARE_NAME, {}).get('mana_cost', ''), pips)


def _castable_tutor(seen, pips, card_data, card_tags):
    """
    Return the name of the first castable creature tutor in `seen`, or None.
    (Tutors are only considered when Sunmare has not yet been drawn naturally.)
    """
    for card in seen:
        if card == SUNMARE_NAME:
            continue
        if TUTOR_TAG in card_tags.get(card, []):
            if _satisfies(card_data.get(card, {}).get('mana_cost', ''), pips):
                return card
    return None


def _best_enabler(seen, pips, card_data, card_tags):
    """
    Return the name of the cheapest castable lifelink/lifegain card in `seen`,
    preferring lifelink over lifegain on a tie, or None if none is castable.
    """
    best      = None
    best_cmc  = 999
    best_pref = 0  # higher = preferred

    for card in seen:
        tags = card_tags.get(card, [])
        is_lifelink = 'lifelink' in tags
        is_lifegain = 'lifegain' in tags
        if not (is_lifelink or is_lifegain):
            continue
        cost_str = card_data.get(card, {}).get('mana_cost', '')
        cost     = parse_mana_cost(cost_str)
        if cost is None:
            continue
        cmc = sum(cost.values())
        if not _satisfies(cost_str, pips):
            continue
        pref = 1 if is_lifelink else 0
        if cmc < best_cmc or (cmc == best_cmc and pref > best_pref):
            best, best_cmc, best_pref = card, cmc, pref

    return best


# ---------------------------------------------------------------------------
# Per-game simulation
# ---------------------------------------------------------------------------

def simulate_game(shuffled, card_data, card_tags):
    """
    Simulate one game.  Returns (engine_turn, enabler, source) or (None, None, None).

    engine_turn : int   -- first turn engine is online
    enabler     : str   -- lifelink/lifegain card that was in play
    source      : str   -- 'natural' or 'tutor'
    """
    sunmare_in_hand  = False   # Sunmare has been drawn or fetched
    sunmare_source   = None    # 'natural' | 'tutor'
    tutor_fired      = False   # a tutor already resolved; Sunmare arrives next turn

    for turn in range(1, MAX_TURN + 1):
        seen = shuffled[:min(7 + turn + _DRAW_EXTRAS[turn], len(shuffled))]
        pips = _pips_at_turn(seen, turn, card_data, card_tags)

        # Tutor fired last turn -> Sunmare is now in hand
        if tutor_fired and not sunmare_in_hand:
            sunmare_in_hand = True
            sunmare_source  = 'tutor'

        # Natural draw of Sunmare
        if not sunmare_in_hand and SUNMARE_NAME in seen:
            sunmare_in_hand = True
            sunmare_source  = 'natural'

        # Check for a castable tutor (only if Sunmare not yet in hand)
        if not sunmare_in_hand and not tutor_fired:
            if _castable_tutor(seen, pips, card_data, card_tags):
                tutor_fired = True
                # Sunmare arrives in hand next turn; skip engine check this turn

        if not sunmare_in_hand:
            continue

        # Sunmare must be castable this turn
        if not _sunmare_castable(pips, card_data):
            continue

        # A lifelink/lifegain source must also be castable
        enabler = _best_enabler(seen, pips, card_data, card_tags)
        if enabler is None:
            continue

        return turn, enabler, sunmare_source

    return None, None, None


# ---------------------------------------------------------------------------
# Batch simulation
# ---------------------------------------------------------------------------

def simulate(deck, card_data, card_tags):
    turn_counts  = defaultdict(int)
    enabler_hits = defaultdict(int)
    source_hits  = defaultdict(int)
    never_count  = 0

    for i in range(TRIALS):
        if i % 10_000 == 0:
            print(f"  {i:>7,} / {TRIALS:,}", end='\r')
            sys.stdout.flush()

        shuffled = deck[:]
        random.shuffle(shuffled)

        engine_turn, enabler, source = simulate_game(shuffled, card_data, card_tags)

        if engine_turn is not None:
            turn_counts[engine_turn] += 1
            enabler_hits[enabler]    += 1
            source_hits[source]      += 1
        else:
            never_count += 1

    print(f"  {TRIALS:>7,} / {TRIALS:,}  done")
    return turn_counts, enabler_hits, source_hits, never_count


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def bar(pct, width=36):
    filled = int(round(pct / 100 * width))
    return '#' * filled + '.' * (width - filled)


def main():
    deck      = load_deck("deck.txt")
    card_data = load_cards("cards.json")
    card_tags = load_tags("tagged.json")

    print(f"\nSimulating {TRIALS:,} games ...\n")
    turn_counts, enabler_hits, source_hits, never_count = simulate(
        deck, card_data, card_tags
    )

    assembled_total = TRIALS - never_count

    # --- Assembly curve ---
    print(f"\nCrested Sunmare Engine Clock  --  {TRIALS:,} simulated games, up to turn {MAX_TURN}")
    print("=" * 70)
    print(f"\n  {'Turn':<6}  {'This turn':>10}  {'Cumulative':>10}   Bar")
    print(f"  {'-'*6}  {'-'*10}  {'-'*10}   {'-'*36}")

    cumulative = 0.0
    for turn in range(1, MAX_TURN + 1):
        pct_this   = turn_counts[turn] / TRIALS * 100
        cumulative += pct_this
        b = bar(cumulative)
        print(f"  {turn:<6}  {pct_this:>9.1f}%  {cumulative:>9.1f}%   {b}")

    never_pct = never_count / TRIALS * 100
    print(f"  {'Never':<6}  {never_pct:>9.1f}%")

    # --- Enabler breakdown ---
    print(f"\n\nEnablers  (of {assembled_total:,} assembled games, cheapest-first credit)")
    print("-" * 70)

    total_e = sum(enabler_hits.values())
    if total_e > 0:
        for card, count in sorted(enabler_hits.items(), key=lambda x: -x[1]):
            pct    = count / total_e * 100
            tags   = card_tags.get(card, [])
            label  = 'lifelink' if 'lifelink' in tags else 'lifegain'
            print(f"  {card:<30}  [{label:<8}]  {pct:5.1f}%  {bar(pct, 24)}")

    # --- Sunmare source split ---
    print(f"\n\nSunmare source split")
    print("-" * 70)

    total_s = sum(source_hits.values())
    if total_s > 0:
        for source in ('natural', 'tutor'):
            count = source_hits[source]
            pct   = count / total_s * 100
            label = 'Natural draw      ' if source == 'natural' else 'Via creature tutor'
            print(f"  {label}  {pct:5.1f}%  {bar(pct, 30)}")

    print()


if __name__ == "__main__":
    main()
