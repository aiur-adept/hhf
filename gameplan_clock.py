"""
gameplan_clock.py

Simulates 100,000 games and tracks when each gameplan first becomes viable,
up to turn 10.  Uses the same mana model and draw-extra table as
sunmare_clock.py.  Creature tutors can fetch the primary creature for any
gameplan that has one.

Gameplans
---------
  GP1  Crested Sunmare Engine     Sunmare (natural/tutor) + lifelink/lifegain enabler
  GP2  Thundering Mightmare       Mightmare (natural/tutor) + any other castable creature
  GP3  Guardian Sunmare           Guardian (natural/tutor) + saddle power >= 4 available
  GP4  Atla + Roaming Throne      Atla (commander) castable + Roaming Throne in seen/castable
  GP5  Calamity Copy Attack       Calamity (natural/tutor) + any castable creature power >= 1
  GP6  Shadowfax Alpha Strike     Shadowfax (natural/tutor) + castable creature with power <= 3
  GP7  Atla + Egg Cracker        Atla (commander) castable + Egg cracker in seen/castable
  GP8  Atla Trigger Doubler       Atla (commander) castable + any of: Roaming Throne,
                                  Strionic Resonator, or Delney, Streetwise Lookout
  GP9  Atla Infinite Eggs         Atla (commander) castable + Thornbite Staff +
                                  Ashnod's Altar

Atla is treated as always accessible from the command zone for GP3/GP4/GP5/GP7/GP8:
saddle checks add her 2 power to the pool, and GP4/GP7/GP8 only need her cost to
be payable.  Tutors are modelled identically to sunmare_clock.py.
"""

import concurrent.futures
import random
import time
from collections import defaultdict

from analyze import (
    ALL_MANA, DECK_COLORS, _land_colors,
    load_cards, load_deck, load_tags, parse_mana_cost,
)

TRIALS    = 100_000
MAX_TURN  = 10
ATLA      = "Atla Palani, Nest Tender"
TUTOR_TAG = 'tutor-creature'

# Extra cards drawn beyond baseline (7 opening hand + 1/turn), from empirical
# simulation in card_draw_simulator.py.  Index = turn (1-based); index 0 unused.
_DRAW_EXTRAS = [0, 0, 0, 0, 1, 1, 2, 3, 4, 6, 7]

BAD_HAND_FLAGS = [
    ('few_lands',     '<=2 lands'),
    ('no_horses',     'No horses'),
    ('no_creatures',  'Only non-creature spells'),
    ('colour_screwed','Colour-screwed'),
    ('high_cmc',      'Avg CMC >= 4'),
]


# --- Mana helpers (identical to sunmare_clock.py) ----------------------------

def _all_pips(cards, card_data, card_tags):
    pips = []
    for card in cards:
        entry     = card_data.get(card, {})
        type_line = entry.get('type_line', '') or ''
        if 'Land' in type_line:
            colors = _land_colors(entry)
            if colors:
                pips.append(frozenset(colors))
        elif 'mana' in card_tags.get(card, []):
            pips.append(DECK_COLORS)
    return pips


def _pips_at_turn(cards, turn, card_data, card_tags):
    all_p = _all_pips(cards, card_data, card_tags)
    all_p.sort(key=lambda s: -len(s))
    return all_p[:turn]


def _satisfies(cost_str, pips):
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


def _castable(name, card_data, pips):
    return _satisfies(card_data.get(name, {}).get('mana_cost', ''), pips)


def _is_creature(name, card_data):
    tl = card_data.get(name, {}).get('type_line', '') or ''
    return 'Creature' in tl and 'Land' not in tl


def _power(name, card_data):
    try:
        v = card_data.get(name, {}).get('power')
        return int(v) if v is not None else -1
    except (ValueError, TypeError):
        return -1


def _find_tutor(seen, pips, card_data, card_tags, exclude):
    """Return any castable creature tutor from seen, ignoring the excluded card."""
    for card in seen:
        if card == exclude:
            continue
        if TUTOR_TAG in card_tags.get(card, []) and _castable(card, card_data, pips):
            return card
    return None


# --- Bad-hand characterisation ------------------------------------------------

def characterize_hand(opening7, card_data, card_tags):
    """Return frozenset of bad-hand flag keys for the opening 7 cards."""
    flags = set()

    lands     = [c for c in opening7
                 if 'Land' in (card_data.get(c, {}).get('type_line', '') or '')]
    non_lands = [c for c in opening7 if c not in set(lands)]
    creatures = [c for c in non_lands if _is_creature(c, card_data)]

    # <=2 lands
    if len(lands) <= 2:
        flags.add('few_lands')

    # no horses (uses 'horses' tag from tagged.json)
    if not any('horses' in card_tags.get(c, []) for c in opening7):
        flags.add('no_horses')

    # only non-creature spells (has spells but none are creatures)
    if non_lands and not creatures:
        flags.add('no_creatures')

    # colour-screwed: has non-lands but can't cast any with the lands in hand
    if non_lands:
        land_pips = []
        for c in lands:
            colors = _land_colors(card_data.get(c, {}))
            if colors:
                land_pips.append(frozenset(colors))
        land_pips.sort(key=lambda s: -len(s))
        castable_any = any(
            _satisfies(card_data.get(c, {}).get('mana_cost', ''), land_pips)
            for c in non_lands
        )
        if not castable_any:
            flags.add('colour_screwed')

    # avg CMC >= 4 (non-land cards only)
    if non_lands:
        cmcs = []
        for c in non_lands:
            parsed = parse_mana_cost(card_data.get(c, {}).get('mana_cost', ''))
            cmcs.append(sum(parsed.values()) if parsed else 0)
        if sum(cmcs) / len(cmcs) >= 4:
            flags.add('high_cmc')

    return frozenset(flags)


# --- Gameplan registry --------------------------------------------------------

# (id, display label padded to 26 chars)
GAMEPLANS = [
    ('gp1', 'Crested Sunmare Engine    '),
    ('gp2', 'Thundering Mightmare      '),
    ('gp3', 'Guardian Sunmare          '),
    ('gp4', 'Atla + Roaming Throne     '),
    ('gp5', 'Calamity Copy Attack      '),
    ('gp6', 'Shadowfax Alpha Strike    '),
    ('gp7', 'Atla + Skullclamp Turbo   '),
    ('gp8', 'Atla Trigger Doubler      '),
    ('gp9', 'Atla Infinite Eggs        '),
]

# Primary creatures that creature tutors can fetch.
# GP4 and GP7 use the commander (always accessible); no primary needed.
PRIMARIES = {
    'gp1': 'Crested Sunmare',
    'gp2': 'Thundering Mightmare',
    'gp3': 'Guardian Sunmare',
    'gp5': 'Calamity, Galloping Inferno',
    'gp6': 'Shadowfax, Lord of Horses',
}


# --- Per-game simulation ------------------------------------------------------

def simulate_game(shuffled, card_data, card_tags):
    """Return dict: gameplan_id -> first turn online (int), or None."""
    in_hand     = {gp: False for gp in PRIMARIES}
    tutor_fired = {gp: False for gp in PRIMARIES}
    results     = {gp_id: None for gp_id, _ in GAMEPLANS}

    for turn in range(1, MAX_TURN + 1):
        seen = shuffled[:min(7 + turn + _DRAW_EXTRAS[turn], len(shuffled))]
        pips = _pips_at_turn(seen, turn, card_data, card_tags)

        # Advance tutor model for each primary-creature gameplan
        for gp, name in PRIMARIES.items():
            if tutor_fired[gp] and not in_hand[gp]:
                in_hand[gp] = True
            if not in_hand[gp] and name in seen:
                in_hand[gp] = True
            if not in_hand[gp] and not tutor_fired[gp]:
                if _find_tutor(seen, pips, card_data, card_tags, name):
                    tutor_fired[gp] = True

        # GP1: Crested Sunmare + lifelink/lifegain enabler
        if results['gp1'] is None and in_hand['gp1']:
            if _castable('Crested Sunmare', card_data, pips):
                for c in seen:
                    tags = card_tags.get(c, [])
                    if ('lifelink' in tags or 'lifegain' in tags) and _castable(c, card_data, pips):
                        results['gp1'] = turn
                        break

        # GP2: Thundering Mightmare + any soulbond partner
        if results['gp2'] is None and in_hand['gp2']:
            if _castable('Thundering Mightmare', card_data, pips):
                for c in seen:
                    if c != 'Thundering Mightmare' and _is_creature(c, card_data) and _castable(c, card_data, pips):
                        results['gp2'] = turn
                        break

        # GP3: Guardian Sunmare + saddle power >= 4
        # Atla contributes her 2 power from the command zone.
        if results['gp3'] is None and in_hand['gp3']:
            if _castable('Guardian Sunmare', card_data, pips):
                saddle = 2 if _castable(ATLA, card_data, pips) else 0
                for c in seen:
                    if c in ('Guardian Sunmare', ATLA) or not _is_creature(c, card_data):
                        continue
                    if _castable(c, card_data, pips) and _power(c, card_data) > 0:
                        saddle += _power(c, card_data)
                if saddle >= 4:
                    results['gp3'] = turn

        # GP4: Atla (commander) + Roaming Throne
        if results['gp4'] is None:
            if _castable(ATLA, card_data, pips):
                if 'Roaming Throne' in seen and _castable('Roaming Throne', card_data, pips):
                    results['gp4'] = turn

        # GP5: Calamity + saddle-1 creature (power >= 1)
        # Atla (2/3, power 2) always available as saddle fodder.
        if results['gp5'] is None and in_hand['gp5']:
            if _castable('Calamity, Galloping Inferno', card_data, pips):
                saddle_ok = _castable(ATLA, card_data, pips)
                if not saddle_ok:
                    for c in seen:
                        if c != 'Calamity, Galloping Inferno' and _is_creature(c, card_data):
                            if _castable(c, card_data, pips) and _power(c, card_data) >= 1:
                                saddle_ok = True
                                break
                if saddle_ok:
                    results['gp5'] = turn

        # GP6: Shadowfax + creature with power <= 3 in hand
        # Shadowfax is 4/4; can cheat in creatures with strictly lesser power.
        if results['gp6'] is None and in_hand['gp6']:
            if _castable('Shadowfax, Lord of Horses', card_data, pips):
                for c in seen:
                    if c == 'Shadowfax, Lord of Horses' or not _is_creature(c, card_data):
                        continue
                    if _castable(c, card_data, pips) and 0 <= _power(c, card_data) <= 3:
                        results['gp6'] = turn
                        break

        # GP7: Atla (commander) + Egg cracker
        if results['gp7'] is None:
            if _castable(ATLA, card_data, pips):
                if ('Skullclamp' in seen and _castable('Skullclamp', card_data, pips) or
                    ('Ashnod\'s Altar' in seen and _castable('Ashnod\'s Altar', card_data, pips)) or
                    ('Goblin Bombardment' in seen and _castable('Goblin Bombardment', card_data, pips))
                ):
                    results['gp7'] = turn

        # GP8: Atla (commander) + trigger doubler
        # Roaming Throne, Strionic Resonator, or Delney each double/copy Atla's egg trigger.
        if results['gp8'] is None:
            if _castable(ATLA, card_data, pips):
                doubler_ok = (
                    ('Roaming Throne' in seen and _castable('Roaming Throne', card_data, pips)) or
                    ('Strionic Resonator' in seen and _castable('Strionic Resonator', card_data, pips)) or
                    ('Delney, Streetwise Lookout' in seen and _castable('Delney, Streetwise Lookout', card_data, pips))
                )
                if doubler_ok:
                    results['gp8'] = turn

        # GP9: Atla (commander) + Thornbite Staff + Ashnod's Altar
        if results['gp9'] is None:
            if _castable(ATLA, card_data, pips):
                if ('Thornbite Staff' in seen and _castable('Thornbite Staff', card_data, pips) and
                    "Ashnod's Altar" in seen and _castable("Ashnod's Altar", card_data, pips)):
                    results['gp9'] = turn

    return results


# --- Batch run ----------------------------------------------------------------

N_WORKERS = 12


def _run_chunk(args):
    """Simulate a slice of games in a worker process; returns partial aggregated results."""
    deck, card_data, card_tags, seeds = args

    flag_keys    = [k for k, _ in BAD_HAND_FLAGS]
    turn_counts  = {gp_id: defaultdict(int) for gp_id, _ in GAMEPLANS}
    first_any_on = defaultdict(int)
    no_gp_games  = 0
    flag_total   = defaultdict(int)
    flag_no_gp   = defaultdict(int)
    nogp_flags   = defaultdict(int)
    nogp_other   = 0

    rng = random.Random()
    for seed in seeds:
        rng.seed(seed)
        shuffled = deck[:]
        rng.shuffle(shuffled)
        hand_flags = characterize_hand(shuffled[:7], card_data, card_tags)
        game       = simulate_game(shuffled, card_data, card_tags)

        earliest = None
        for gp_id, _ in GAMEPLANS:
            t = game[gp_id]
            if t is not None:
                turn_counts[gp_id][t] += 1
                if earliest is None or t < earliest:
                    earliest = t

        is_no_gp = earliest is None

        if earliest is not None:
            first_any_on[earliest] += 1
        else:
            no_gp_games += 1

        for flag in flag_keys:
            if flag in hand_flags:
                flag_total[flag] += 1
                if is_no_gp:
                    flag_no_gp[flag] += 1
                    nogp_flags[flag] += 1

        if is_no_gp and not hand_flags:
            nogp_other += 1

    return (
        {gp_id: dict(v) for gp_id, v in turn_counts.items()},
        dict(first_any_on), no_gp_games,
        dict(flag_total), dict(flag_no_gp), dict(nogp_flags), nogp_other,
    )


def run(deck, card_data, card_tags):
    base_rng   = random.Random()
    seeds      = [base_rng.randint(0, 2**32 - 1) for _ in range(TRIALS)]
    chunk_size = (TRIALS + N_WORKERS - 1) // N_WORKERS
    chunks     = [seeds[i:i + chunk_size] for i in range(0, TRIALS, chunk_size)]

    turn_counts  = {gp_id: defaultdict(int) for gp_id, _ in GAMEPLANS}
    first_any_on = defaultdict(int)
    no_gp_games  = 0
    flag_total   = defaultdict(int)
    flag_no_gp   = defaultdict(int)
    nogp_flags   = defaultdict(int)
    nogp_other   = 0

    t0 = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = [
            executor.submit(_run_chunk, (deck, card_data, card_tags, chunk))
            for chunk in chunks
        ]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            (chunk_tc, chunk_fao, chunk_no_gp,
             chunk_ft, chunk_fng, chunk_nf, chunk_no) = future.result()
            for gp_id, counts in chunk_tc.items():
                for t, cnt in counts.items():
                    turn_counts[gp_id][t] += cnt
            for t, cnt in chunk_fao.items():
                first_any_on[t] += cnt
            no_gp_games += chunk_no_gp
            for flag, cnt in chunk_ft.items():
                flag_total[flag] += cnt
            for flag, cnt in chunk_fng.items():
                flag_no_gp[flag] += cnt
            for flag, cnt in chunk_nf.items():
                nogp_flags[flag] += cnt
            nogp_other += chunk_no
            print(f"  Worker {i}/{len(chunks)} done", flush=True)

    print(f"\nFinished in {time.time() - t0:.1f}s\n")
    return (turn_counts, first_any_on, no_gp_games,
            flag_total, flag_no_gp, nogp_flags, nogp_other)


# --- Reporting ----------------------------------------------------------------

def _cumul(counts, total):
    """Cumulative % by each turn."""
    result, running = {}, 0
    for t in range(1, MAX_TURN + 1):
        running += counts.get(t, 0)
        result[t] = running / total * 100
    return result


def _bar(pct, width=32):
    filled = int(round(pct / 100 * width))
    return '#' * filled + '.' * (width - filled)


def main():
    deck      = load_deck('deck.txt')
    card_data = load_cards('cards.json')
    card_tags = load_tags('tagged.json')

    print(f"\nSimulating {TRIALS:,} games ...\n")
    (turn_counts, first_any_on, no_gp_games,
     flag_total, flag_no_gp, nogp_flags, nogp_other) = run(deck, card_data, card_tags)

    w = 74
    print(f"Gameplan Assembly Clock  --  {TRIALS:,} simulated games, up to turn {MAX_TURN}")
    print('=' * w)

    # Per-gameplan detail curves
    for gp_id, label in GAMEPLANS:
        c = _cumul(turn_counts[gp_id], TRIALS)
        print(f"\n  {label.strip()}")
        print(f"  {'Turn':<6}  {'This turn':>10}  {'Cumulative':>10}   Bar")
        print(f"  {'-'*6}  {'-'*10}  {'-'*10}   {'-'*32}")
        prev = 0.0
        for t in range(1, MAX_TURN + 1):
            this = c[t] - prev
            print(f"  {t:<6}  {this:>9.1f}%  {c[t]:>9.1f}%   {_bar(c[t])}")
            prev = c[t]
        print(f"  {'Never':<6}  {100 - c[MAX_TURN]:>9.1f}%")

    # Summary table
    snap = [5, 7, 10]
    print(f"\n\n{'=' * w}")
    print(f"  Summary -- cumulative % online by turn")
    print(f"{'=' * w}")
    print(f"  {'Gameplan':<28}" + "".join(f"  {'T'+str(t):>6}" for t in snap) + f"  {'Never':>7}")
    print(f"  {'-'*28}" + "".join(f"  {'------':>6}" for _ in snap) + f"  {'-------':>7}")

    for gp_id, label in GAMEPLANS:
        c     = _cumul(turn_counts[gp_id], TRIALS)
        never = 100 - c[MAX_TURN]
        vals  = "".join(f"  {c[t]:>5.1f}%" for t in snap)
        print(f"  {label:<28}{vals}  {never:>6.1f}%")

    print(f"  {'-'*28}" + "".join(f"  {'------':>6}" for _ in snap) + f"  {'-------':>7}")
    c_any     = _cumul(first_any_on, TRIALS)
    never_any = 100 - c_any[MAX_TURN]
    print(f"  {'>=1 gameplan online':<28}" + "".join(f"  {c_any[t]:>5.1f}%" for t in snap) + f"  {never_any:>6.1f}%")
    print(f"  {'No gameplan online':<28}" + "".join(f"  {100-c_any[t]:>5.1f}%" for t in snap) + f"  {no_gp_games/TRIALS*100:>6.1f}%")

    # --- Bad-hand breakdown among unproductive games ---
    print(f"\n\n{'=' * w}")
    print(f"  Bad-hand breakdown  --  among {no_gp_games:,} games with no gameplan")
    print(f"{'=' * w}")
    print(f"  (Flags can overlap; percentages do not sum to 100%)")
    print(f"  {'Flag':<28}  {'Count':>7}  {'% of no-gameplan games':>22}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*22}")
    for flag, label in BAD_HAND_FLAGS:
        n   = nogp_flags[flag]
        pct = n / no_gp_games * 100 if no_gp_games else 0.0
        print(f"  {label:<28}  {n:>7,}  {pct:>21.1f}%")
    other_pct = nogp_other / no_gp_games * 100 if no_gp_games else 0.0
    print(f"  {'Other (no bad signal)':<28}  {nogp_other:>7,}  {other_pct:>21.1f}%")

    # --- Unplayability rate by bad-hand signal ---
    print(f"\n\n{'=' * w}")
    print(f"  Unplayability rate  --  P(no gameplan | flag present in opening 7)")
    print(f"{'=' * w}")
    print(f"  {'Flag':<28}  {'Hands':>7}  {'No-GP':>7}  {'Rate':>6}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*6}")
    for flag, label in BAD_HAND_FLAGS:
        total = flag_total[flag]
        no_gp = flag_no_gp[flag]
        rate  = no_gp / total * 100 if total else 0.0
        print(f"  {label:<28}  {total:>7,}  {no_gp:>7,}  {rate:>5.1f}%")
    print(f"  {'-'*28}  {'-'*7}  {'-'*7}  {'-'*6}")
    baseline = no_gp_games / TRIALS * 100
    print(f"  {'Overall (baseline)':<28}  {TRIALS:>7,}  {no_gp_games:>7,}  {baseline:>5.1f}%")
    print()


if __name__ == '__main__':
    main()
