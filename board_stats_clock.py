#!/usr/bin/env python3
"""
board_stats_clock.py

Simulates 100,000 goldfish games and reports median and IQR (Q75-Q25) of
total creature power and toughness on board at the end of each turn, T1-T10.
Median and IQR are used instead of mean/SD to avoid distortion from
high-variance outlier games (e.g. Colossification on a large creature).

Board modelling
---------------
- 99-card deck; Atla Palani, Nest Tender in the command zone (not in the 99).
- Opening hand 7; draw 1/turn + empirical extras (_DRAW_EXTRAS from gameplan_clock.py).
- Mana: land-drop per turn; mana rocks cast in a priority phase; mana dorks cast
  in a priority phase (contribute mana from the following turn).
- Land-fetch sorceries (Nature's Lore / Three Visits: untapped; Farseek: tapped)
  handled in the priority phase.
- After the priority phase, a greedy casting loop executes actions ranked by
  estimated delta-power added to the board.  Ties are broken by uniform random.

Actions modelled
----------------
  Creatures          : base power as delta
  Equipment          : cast + equip same turn (delta = pow_bonus) if mana allows;
                       cast only (delta = pow_bonus * 0.4) otherwise; equip next
                       turn once already in play (delta = pow_bonus)
  Collective Blessing: delta = 3 * creature_count
  Colossification    : delta = 20; targets the highest-power creature
  Commander (Atla)   : always available, delta = 2

Effects tracked
---------------
  Equipment p/t bonuses applied to equipped bearer
  Colossification (+20/+20) on enchanted creature
  Collective Blessing (+3/+3 to all creatures)
  Thundering Mightmare soulbond: +3 counters/turn to each bonded creature
  Chrome Steed metalcraft: +2/+2 when 3+ artifacts in play (dynamic)
  Faeburrow Elder treated as 2/2 (base value is 0/0 in card data)

Goldfish assumptions
--------------------
  No removal, no combat losses.  Egg tokens and Atla hatch triggers NOT modelled.
  Colour constraints ignored.  Standard deviations are population SD (N = TRIALS).
"""

import concurrent.futures
import random
import time

from analyze import load_cards, load_deck, load_tags, parse_mana_cost

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRIALS    = 100_000
MAX_TURN  = 10
N_WORKERS = 12

ATLA      = "Atla Palani, Nest Tender"
ATLA_BASE = 4   # {1}{R}{G}{W}

# Cumulative extra draws beyond baseline (7 opening + 1/turn); index = turn.
_DRAW_EXTRAS = [0, 0, 0, 0, 1, 1, 2, 3, 4, 6, 7]
_DRAW_INC    = [0] + [
    _DRAW_EXTRAS[t] - _DRAW_EXTRAS[t - 1]
    for t in range(1, MAX_TURN + 1)
]

_FETCH_UNTAPPED = frozenset(["Nature's Lore", "Three Visits"])
_FETCH_TAPPED   = frozenset(["Farseek"])
_ALL_FETCHES    = _FETCH_UNTAPPED | _FETCH_TAPPED

# Equipment: name -> (pow_bonus, tou_bonus)
_EQUIP_PT = {
    'Heirloom Blade':            ( 3,  1),
    'Loxodon Warhammer':         ( 3,  0),
    'Sword of Feast and Famine': ( 2,  2),
    'Sword of Fire and Ice':     ( 2,  2),
    'Shadowspear':               ( 1,  1),
    'Skullclamp':                ( 1, -1),
    'Fireshrieker':              ( 0,  0),
    'Basilisk Collar':           ( 0,  0),
    'Resurrection Orb':          ( 0,  0),
    'Swiftfoot Boots':           ( 0,  0),
}

# Equipment equip costs (generic integer).
_EQUIP_COST = {
    'Heirloom Blade':            1,
    'Loxodon Warhammer':         3,
    'Sword of Feast and Famine': 2,
    'Sword of Fire and Ice':     2,
    'Shadowspear':               2,
    'Skullclamp':                1,
    'Fireshrieker':              2,
    'Basilisk Collar':           2,
    'Resurrection Orb':          4,
    'Swiftfoot Boots':           1,
}

# Base power/toughness overrides for */* cards.
_PT_OVERRIDES = {
    'Faeburrow Elder': (2, 2),  # stored 0/0 in card data; 2/2 is conservative
}

COLLECTIVE_BLESSING = 'Collective Blessing'
COLOSSIFICATION     = 'Colossification'
MIGHTMARE           = 'Thundering Mightmare'

# Modelled soulbond counter gain per turn once bonded.
SOULBOND_COUNTERS_PER_TURN = 3


# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------

def _cmc(name, card_data):
    cost = parse_mana_cost(card_data.get(name, {}).get('mana_cost', ''))
    return 0 if cost is None else sum(cost.values())


def _is_land(name, card_data):
    return 'Land' in (card_data.get(name, {}).get('type_line', '') or '')


def _is_creature(name, card_data):
    tl = card_data.get(name, {}).get('type_line', '') or ''
    return 'Creature' in tl and 'Land' not in tl


def _is_equipment(name, card_data):
    return 'Equipment' in (card_data.get(name, {}).get('type_line', '') or '')


def _is_artifact(name, card_data):
    return 'Artifact' in (card_data.get(name, {}).get('type_line', '') or '')


def _int_stat(name, key, card_data):
    """Integer power or toughness; respects _PT_OVERRIDES."""
    if name in _PT_OVERRIDES:
        return _PT_OVERRIDES[name][0 if key == 'power' else 1]
    try:
        v = card_data.get(name, {}).get(key)
        return int(v) if v is not None else 0
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Board helpers
# ---------------------------------------------------------------------------

def _artifact_count(creatures, equipment_in_play, rocks_in_play, card_data):
    """Count artifact permanents on the battlefield."""
    n = 0
    for ce in creatures:
        if _is_artifact(ce['name'], card_data):
            n += 1
        n += len(ce['equipment'])          # each attached piece is an artifact
    n += len(equipment_in_play)            # unattached equipment
    for name in rocks_in_play:
        if _is_artifact(name, card_data):
            n += 1
    return n


def _eff_pt(ce, creatures, equipment_in_play, rocks_in_play,
            collective_blessing, coloss_on, card_data):
    """Effective (power, toughness) for one creature dict."""
    p = ce['base_pow'] + ce['counters']
    t = ce['base_tou'] + ce['counters']
    for eq in ce['equipment']:
        pb, tb = _EQUIP_PT.get(eq, (0, 0))
        p += pb
        t += tb
    if coloss_on == ce['name']:
        p += 20
        t += 20
    if collective_blessing:
        p += 3
        t += 3
    if ce['name'] == 'Chrome Steed':
        if _artifact_count(creatures, equipment_in_play, rocks_in_play, card_data) >= 3:
            p += 2
            t += 2
    return p, t


def _board_totals(creatures, equipment_in_play, rocks_in_play,
                  collective_blessing, coloss_on, card_data):
    total_p = total_t = 0
    for ce in creatures:
        p, t = _eff_pt(ce, creatures, equipment_in_play, rocks_in_play,
                       collective_blessing, coloss_on, card_data)
        total_p += p
        total_t += t
    return total_p, total_t


def _best_equip_target(equip_name, creatures, equipment_in_play, rocks_in_play,
                       collective_blessing, coloss_on, card_data):
    """Highest-power creature that won't die from the equipment."""
    _, tou_b = _EQUIP_PT.get(equip_name, (0, 0))
    best_pow, best_ce = -1, None
    for ce in creatures:
        _, eff_t = _eff_pt(ce, creatures, equipment_in_play, rocks_in_play,
                           collective_blessing, coloss_on, card_data)
        if eff_t + tou_b < 1:   # would die
            continue
        eff_p, _ = _eff_pt(ce, creatures, equipment_in_play, rocks_in_play,
                           collective_blessing, coloss_on, card_data)
        if eff_p > best_pow:
            best_pow, best_ce = eff_p, ce
    return best_ce


def _maybe_soulbond(creatures, soulbond_pair, card_data):
    """
    Try to form a soulbond pair if Thundering Mightmare is on board unpaired.
    Returns updated soulbond_pair (may be unchanged).
    """
    if soulbond_pair is not None:
        return soulbond_pair
    if not any(c['name'] == MIGHTMARE for c in creatures):
        return None
    others = [c for c in creatures if c['name'] != MIGHTMARE]
    if not others:
        return None
    partner = max(others, key=lambda c: c['base_pow'])
    return (MIGHTMARE, partner['name'])


# ---------------------------------------------------------------------------
# Action builder (greedy casting phase)
# ---------------------------------------------------------------------------

def _build_actions(hand, state, mana, card_data, card_tags, rng):
    """
    Return list of candidate actions sorted best-first (delta_pow desc, random tiebreak).
    Each entry: (delta_pow, cost, action_type, card_name).
    """
    candidates = []
    creature_count = len(state['creatures'])

    for card in hand:
        cast_cost = _cmc(card, card_data)

        # ---- Creature (not a mana dork, not already on board) ----
        if _is_creature(card, card_data) and not _is_land(card, card_data):
            if 'mana' in card_tags.get(card, []):
                continue    # handled in priority phase
            if any(c['name'] == card for c in state['creatures']):
                continue    # singleton: already cast
            dp = float(_int_stat(card, 'power', card_data))
            candidates.append((dp, cast_cost, 'creature', card))

        # ---- Equipment ----
        elif card in _EQUIP_PT:
            pow_b, _ = _EQUIP_PT[card]
            eq_cost  = _EQUIP_COST[card]
            # Offer cast + immediate equip (higher delta)
            candidates.append((float(pow_b),
                                cast_cost + eq_cost,
                                'cast_equip', card))
            # Offer cast-only fallback (lower delta, cheaper)
            candidates.append((float(pow_b) * 0.4,
                                cast_cost,
                                'cast_only', card))

        # ---- Collective Blessing ----
        elif card == COLLECTIVE_BLESSING and not state['collective_blessing']:
            dp = 3.0 * creature_count
            candidates.append((dp, _cmc(COLLECTIVE_BLESSING, card_data),
                                'collective_blessing', COLLECTIVE_BLESSING))

        # ---- Colossification ----
        elif card == COLOSSIFICATION and state['coloss_on'] is None and creature_count > 0:
            candidates.append((20.0, _cmc(COLOSSIFICATION, card_data),
                                'colossification', COLOSSIFICATION))

    # ---- Commander: Atla (always available when not in play) ----
    if not state['atla_in_play']:
        candidates.append((2.0, state['atla_cost'], 'atla', ATLA))

    # ---- Equip already-in-play equipment ----
    for eq in state['equipment_in_play']:
        pow_b, _ = _EQUIP_PT.get(eq, (0, 0))
        eq_cost  = _EQUIP_COST.get(eq, 99)
        if creature_count > 0:
            candidates.append((float(pow_b), eq_cost, 'equip_already', eq))

    # Sort: best delta first, random tie-break.
    tiebreaks = [rng.random() for _ in candidates]
    candidates = [(*c, tb) for c, tb in zip(candidates, tiebreaks)]
    candidates.sort(key=lambda x: (-x[0], -x[4]))
    # Strip tiebreak from each tuple
    return [(c[0], c[1], c[2], c[3]) for c in candidates]


# ---------------------------------------------------------------------------
# Single-game simulation
# ---------------------------------------------------------------------------

def simulate_game(shuffled, card_data, card_tags, seed):
    hand    = list(shuffled[:7])
    library = list(shuffled[7:])

    # Per-creature records: {name, base_pow, base_tou, counters, equipment: []}
    creatures      = []
    equipment_in_play = []   # unattached equipment names
    rocks_in_play     = set()  # non-creature, non-equip artifacts in play (for art count)
    collective_blessing = False
    coloss_on           = None    # creature name bearing Colossification
    soulbond_pair       = None    # (name_a, name_b)

    mana_per_turn = 0
    lands_in_play = 0
    pending_lands = 0
    dork_pending  = 0
    atla_in_play  = False
    atla_cost     = ATLA_BASE

    # RNG for action tie-breaking (seeded per game for reproducibility)
    rng = random.Random(seed ^ 0xFACE)

    # Shared state dict passed to helpers that need multiple fields.
    def state():
        return {
            'creatures': creatures,
            'equipment_in_play': equipment_in_play,
            'rocks_in_play': rocks_in_play,
            'collective_blessing': collective_blessing,
            'coloss_on': coloss_on,
            'atla_in_play': atla_in_play,
            'atla_cost': atla_cost,
        }

    def add_creature(name):
        nonlocal soulbond_pair
        bp = _int_stat(name, 'power', card_data)
        bt = _int_stat(name, 'toughness', card_data)
        creatures.append({'name': name, 'base_pow': bp, 'base_tou': bt,
                          'counters': 0, 'equipment': []})
        soulbond_pair = _maybe_soulbond(creatures, soulbond_pair, card_data)

    def do_equip(eq_name):
        """Attach equipment to best valid target; leaves unattached if none."""
        target = _best_equip_target(
            eq_name, creatures, equipment_in_play, rocks_in_play,
            collective_blessing, coloss_on, card_data)
        if target is None:
            equipment_in_play.append(eq_name)
            return
        target['equipment'].append(eq_name)

    snapshots = []

    for turn in range(1, MAX_TURN + 1):

        # ---- Untap / upkeep -------------------------------------------------
        lands_in_play  += pending_lands
        pending_lands   = 0
        mana_per_turn  += dork_pending
        dork_pending    = 0

        # Soulbond counter accumulation.
        if soulbond_pair is not None:
            for ce in creatures:
                if ce['name'] in soulbond_pair:
                    ce['counters'] += SOULBOND_COUNTERS_PER_TURN

        # ---- Draw step -------------------------------------------------------
        for _ in range(1 + _DRAW_INC[turn]):
            if library:
                hand.append(library.pop(0))

        # ---- Mana available --------------------------------------------------
        mana = lands_in_play + mana_per_turn

        # ---- Play a land -----------------------------------------------------
        for card in hand:
            if _is_land(card, card_data):
                hand.remove(card)
                lands_in_play += 1
                mana += 1
                break

        # ---- Priority: fetch sorceries (untapped first) ----------------------
        for card in [c for c in hand if c in _FETCH_UNTAPPED]:
            if mana >= 2:
                hand.remove(card)
                mana -= 2
                lands_in_play += 1
                mana += 1
        for card in [c for c in hand if c in _FETCH_TAPPED]:
            if mana >= 2:
                hand.remove(card)
                mana -= 2
                pending_lands += 1

        # ---- Priority: mana rocks (cheapest first) ---------------------------
        rocks = sorted(
            [c for c in hand
             if 'mana' in card_tags.get(c, [])
             and not _is_land(c, card_data)
             and not _is_creature(c, card_data)
             and not _is_equipment(c, card_data)
             and c not in _ALL_FETCHES],
            key=lambda c: _cmc(c, card_data),
        )
        for rock in rocks:
            cost = _cmc(rock, card_data)
            if mana >= cost:
                hand.remove(rock)
                mana -= cost
                bonus = 2 if rock == 'Sol Ring' else 1
                mana_per_turn += bonus
                mana += bonus
                rocks_in_play.add(rock)

        # ---- Priority: mana dorks (cheapest first) ---------------------------
        dorks = sorted(
            [c for c in hand
             if 'mana' in card_tags.get(c, [])
             and _is_creature(c, card_data)
             and not any(x['name'] == c for x in creatures)],
            key=lambda c: _cmc(c, card_data),
        )
        for dork in dorks:
            cost = _cmc(dork, card_data)
            if mana >= cost:
                hand.remove(dork)
                mana -= cost
                add_creature(dork)
                dork_pending += 1

        # ---- Greedy casting loop --------------------------------------------
        # failed_equips: equipment that currently have no safe attach target;
        # excluded from candidates until board state changes.
        failed_equips = set()

        while mana > 0:
            actions = _build_actions(hand, state(), mana, card_data, card_tags, rng)
            affordable = [
                (dp, cost, atype, card)
                for dp, cost, atype, card in actions
                if cost <= mana
                and not (atype == 'equip_already' and card in failed_equips)
            ]
            if not affordable:
                break

            dp, cost, atype, card = affordable[0]

            if atype == 'creature':
                hand.remove(card)
                mana -= cost
                add_creature(card)
                failed_equips.clear()  # new creature may enable previously blocked equips

            elif atype == 'cast_equip':
                # Prefer cast+equip in one turn. Fall back to cast-only if no safe target.
                pow_b, tou_b = _EQUIP_PT.get(card, (0, 0))
                eligible = [c for c in creatures
                            if _eff_pt(c, creatures, equipment_in_play, rocks_in_play,
                                       collective_blessing, coloss_on, card_data)[1] + tou_b >= 1]
                hand.remove(card)
                if eligible:
                    mana -= cost   # cast + equip cost
                    target = max(eligible, key=lambda c: _eff_pt(
                        c, creatures, equipment_in_play, rocks_in_play,
                        collective_blessing, coloss_on, card_data)[0])
                    target['equipment'].append(card)
                else:
                    mana -= _cmc(card, card_data)   # cast cost only; equip deferred
                    equipment_in_play.append(card)

            elif atype == 'cast_only':
                hand.remove(card)
                mana -= cost
                equipment_in_play.append(card)

            elif atype == 'equip_already':
                # Pre-check: confirm a safe target exists before committing mana.
                pow_b, tou_b = _EQUIP_PT.get(card, (0, 0))
                eligible = [c for c in creatures
                            if _eff_pt(c, creatures, equipment_in_play, rocks_in_play,
                                       collective_blessing, coloss_on, card_data)[1] + tou_b >= 1]
                if not eligible:
                    failed_equips.add(card)
                    continue   # don't spend mana; retry loop with this action suppressed
                equipment_in_play.remove(card)
                mana -= cost
                target = max(eligible, key=lambda c: _eff_pt(
                    c, creatures, equipment_in_play, rocks_in_play,
                    collective_blessing, coloss_on, card_data)[0])
                target['equipment'].append(card)

            elif atype == 'collective_blessing':
                hand.remove(card)
                mana -= cost
                collective_blessing = True

            elif atype == 'colossification':
                hand.remove(card)
                mana -= cost
                best = max(creatures, key=lambda c: c['base_pow'] + c['counters'])
                coloss_on = best['name']

            elif atype == 'atla':
                mana -= cost
                atla_in_play = True
                atla_cost   += 2
                add_creature(ATLA)
                failed_equips.clear()  # Atla entering may enable blocked equips

        # ---- End-of-turn snapshot -------------------------------------------
        p, t = _board_totals(creatures, equipment_in_play, rocks_in_play,
                              collective_blessing, coloss_on, card_data)
        snapshots.append((p, t))

    return snapshots


# ---------------------------------------------------------------------------
# Batch run (multiprocessed)
# ---------------------------------------------------------------------------

def _run_chunk(args):
    """
    Simulate a slice of games in a worker process.
    Returns raw per-turn value lists so the main process can compute
    median and IQR (no approximation from partial aggregation).
    """
    deck, card_data, card_tags, seeds = args

    pow_vals   = [[] for _ in range(MAX_TURN)]
    tou_vals   = [[] for _ in range(MAX_TURN)]
    over40_games = 0   # games where power reached >= 40 at any turn

    rng = random.Random()
    for seed in seeds:
        rng.seed(seed)
        shuffled = deck[:]
        rng.shuffle(shuffled)
        snaps = simulate_game(shuffled, card_data, card_tags, seed)
        game_over40 = False
        for t, (p, tou) in enumerate(snaps):
            pow_vals[t].append(p)
            tou_vals[t].append(tou)
            if p >= 40:
                game_over40 = True
        if game_over40:
            over40_games += 1

    return pow_vals, tou_vals, over40_games


def run(deck, card_data, card_tags):
    from statistics import quantiles

    base_rng   = random.Random()
    seeds      = [base_rng.randint(0, 2**32 - 1) for _ in range(TRIALS)]
    chunk_size = (TRIALS + N_WORKERS - 1) // N_WORKERS
    chunks     = [seeds[i:i + chunk_size] for i in range(0, TRIALS, chunk_size)]

    all_pow      = [[] for _ in range(MAX_TURN)]
    all_tou      = [[] for _ in range(MAX_TURN)]
    total_over40 = 0

    t0 = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = [
            executor.submit(_run_chunk, (deck, card_data, card_tags, chunk))
            for chunk in chunks
        ]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            ps, ts, chunk_over40 = future.result()
            for t in range(MAX_TURN):
                all_pow[t].extend(ps[t])
                all_tou[t].extend(ts[t])
            total_over40 += chunk_over40
            print(f"  Worker {i}/{len(chunks)} done", flush=True)

    print(f"\nFinished in {time.time() - t0:.1f}s\n")

    results = []
    for t in range(MAX_TURN):
        # quantiles(data, n=4) returns [Q1, Q2, Q3] = [P25, P50, P75]
        qp = quantiles(all_pow[t], n=4)
        qt = quantiles(all_tou[t], n=4)
        results.append((
            qp[1],           # median power
            qp[2] - qp[0],  # IQR power  (Q3 - Q1)
            qt[1],           # median toughness
            qt[2] - qt[0],  # IQR toughness
        ))
    return results, total_over40


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

BAR_WIDTH = 28
BAR_SCALE = 24.0   # max value shown as full bar


def _bar(val, scale=BAR_SCALE, width=BAR_WIDTH):
    filled = int(round(min(val, scale) / scale * width))
    return '#' * filled + '.' * (width - filled)


def main():
    deck      = load_deck('deck.txt')
    card_data = load_cards('cards.json')
    card_tags = load_tags('tagged.json')

    assert len(deck) == 99, f"Expected 99-card deck, got {len(deck)}"

    print(f"\nSimulating {TRIALS:,} games ...\n")
    results, over40_games = run(deck, card_data, card_tags)

    w = 82
    print('=' * w)
    print(f"  Board Power & Toughness Clock  --  {TRIALS:,} games, up to turn {MAX_TURN}")
    print('=' * w)
    print()
    print(f"  {'Turn':<6}  {'Med Pow':>7}  {'IQR':>6}  "
          f"{'Med Tou':>7}  {'IQR':>6}  "
          f"  Power bar (0-{int(BAR_SCALE)})")
    sep = (f"  {'-'*6}  {'-'*7}  {'-'*6}  "
           f"{'-'*7}  {'-'*6}  "
           f"  {'-'*BAR_WIDTH}")
    print(sep)
    for t, (med_p, iqr_p, med_t, iqr_t) in enumerate(results, 1):
        print(f"  {f'T{t}':<6}  {med_p:>7.2f}  {iqr_p:>6.2f}  "
              f"{med_t:>7.2f}  {iqr_t:>6.2f}  "
              f"  {_bar(med_p)}")
    print(sep)
    print()
    over40_pct = over40_games / TRIALS * 100
    print(f"  >= 40 power reached by T{MAX_TURN}: {over40_games:,} games ({over40_pct:.1f}%)")
    print()
    print(f"  Notes:")
    print(f"  - Goldfish: no removal, no combat losses.")
    print(f"  - Median and IQR (Q75-Q25) are robust to Colossification outliers.")
    print(f"  - Equipment equipped to highest-power creature (Skullclamp: tou >= 2 required).")
    print(f"  - Colossification (+20/+20) targets highest-power creature.")
    print(f"  - Collective Blessing (+3/+3 all) valued at 3 x creature count at cast time.")
    print(f"  - Thundering Mightmare soulbond: +{SOULBOND_COUNTERS_PER_TURN} counters/turn to each bonded pair.")
    print(f"  - Chrome Steed gets +2/+2 when 3+ artifacts are in play (dynamic).")
    print(f"  - Faeburrow Elder treated as 2/2.  Egg tokens / hatch creatures NOT included.")
    print(f"  - Statistics computed over {TRIALS:,} games.")
    print()


if __name__ == '__main__':
    main()
