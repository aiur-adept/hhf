#!/usr/bin/env python3
"""
card_draw_simulator.py — Atla Palani "Hatched Horses Fighting" draw analysis
Goldfishes 100,000 hands through turn 10, tracking card draw by source.

Modeling notes
──────────────
• No mulligans. Atla Palani is in the command zone (not in the 99).
• Mana is tracked as a simple integer (color constraints rarely bind
  in a 3-color deck with 37 lands and good fixing; the simplification
  lets us keep ramp interactions clear).
• Summoning sickness: mana-dork creatures and Selvala don't produce
  mana / activate the turn they enter.
• Farseek enters tapped; Nature's Lore and Three Visits enter untapped.

Opponent-determined draw pieces
────────────────────────────────
• Esper Sentinel: total lifetime draws before removal are sampled from
  a distribution with mean ≈ 2.5 (weights across [1–5]).  Each turn
  the sentinel is alive, ~60 % of opponent turns trigger it (per-opponent
  spell frequency), spread across 3 opponents = up to 3 triggers/cycle.
• Mind's Eye: 3 opponents each draw once per turn; you pay {1} per
  trigger.  Mana is drawn from the current turn's pool, representing
  open mana held across opponents' turns.
• Sword of Fire and Ice: 75 % connection rate per turn when equipped
  (clear lines in the mid-game goldfish model).
"""

import argparse
import concurrent.futures
import multiprocessing
import random
from collections import defaultdict
from statistics import mean, stdev
import time

from analyze import load_cards, load_deck, parse_mana_cost

# ── Card identity sets ────────────────────────────────────────────────────────

DRAW_PIECES = frozenset([
    "Esper Sentinel",
    "Skullclamp",
    "Sylvan Library",
    "Selvala, Explorer Returned",
    "Sword of Fire and Ice",
    "Mind's Eye",
    "Return of the Wildspeaker",
])

HORSES = frozenset([
    "Diamond Mare", "Shield Mare", "Bill the Pony", "Brightfield Mustang",
    "Chrome Steed", "Vine Mare", "Crested Sunmare", "Guardian Sunmare",
    "Motivated Pony", "Shadowfax, Lord of Horses", "Thundering Mightmare",
    "Calamity, Galloping Inferno",
])

# Ramp pieces (non-land cards tagged 'mana') that produce extra mana per turn
# once in play.  Values are simplified fixed mana bonus per turn.
RAMP_BONUS = {
    "Sol Ring":        2,
    "Arcane Signet":   1,
    "Birds of Paradise": 1,   # summoning sick turn 1
    "Faeburrow Elder": 2,     # summoning sick turn 1; assumes 2+ colors in play
}

# Land-fetch sorceries: add to lands_in_play directly (untapped) or next turn.
LAND_FETCH_UNTAPPED = frozenset(["Nature's Lore", "Three Visits"])
LAND_FETCH_TAPPED   = frozenset(["Farseek"])

HUMAN_CREATURES = frozenset([
    "Esper Sentinel", "Selvala, Explorer Returned", "Atla Palani",
])

ATLA_BASE_COST  = 4   # {1}{R}{G}{W}
EGG_COST        = 2   # {2} + T to create egg
SKULLCLAMP_EQUIP = 1  # equip cost


# ── Mana cost lookup ──────────────────────────────────────────────────────────

def compute_cmc(card_name: str, card_data: dict) -> int:
    """CMC from card_data, falling back to zero for lands / unknowns."""
    entry = card_data.get(card_name, {})
    cost = parse_mana_cost(entry.get("mana_cost", ""))
    if cost is None:
        return 0
    return sum(v for k, v in cost.items() if k != "generic") + cost.get("generic", 0)


def is_land(card_name: str, card_data: dict) -> bool:
    entry = card_data.get(card_name, {})
    return "Land" in (entry.get("type_line") or "")


# ── Single-game simulation ────────────────────────────────────────────────────

def simulate_game(deck: list[str], card_data: dict, seed: int):
    """
    Simulate one goldfish game through turn 10.
    Returns:
        draws_by_source   dict[str, int]  — total cards drawn per source
        cumulative_totals list[int]       — running total after each turn
    """
    rng = random.Random(seed)
    library = list(deck)
    rng.shuffle(library)
    hand: list[str] = library[:7]
    library = library[7:]
    hand: list[str] = library[:7]
    library = library[7:]

    draws: dict = defaultdict(int)
    draws["opening_hand"] = 7

    # Board state
    bf: set[str]       = set()   # permanents in play (unique names)
    creatures: set[str] = set()  # creature subset

    lands_in_play   = 0
    ramp_mana       = 0   # bonus mana / turn from ramp permanents
    pending_lands   = 0   # tapped lands → arrive next turn

    # Commander (command zone)
    atla_on_bf   = False
    atla_cost    = ATLA_BASE_COST

    # Draw-engine states
    skullclamp_on    = False
    sylvan_on        = False
    selvala_on       = False
    selvala_sick     = False   # summoning sickness
    sword_on         = False
    sword_equipped   = False
    minds_eye_on     = False
    # Esper Sentinel: None when not in play, else remaining lifetime draws
    sentinel_left: int | None = None
    sentinel_sick    = False

    mana = 0  # current-turn mana pool (mutated by nested helpers)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def draw_card(source: str) -> None:
        if library:
            hand.append(library.pop(0))
            draws[source] += 1

    def try_cast(card: str, cost: int) -> bool:
        """Cast card if it's in hand and we have mana; returns success."""
        nonlocal mana
        if card in hand and mana >= cost:
            hand.remove(card)
            mana -= cost
            bf.add(card)
            return True
        return False

    # ── Turn loop ─────────────────────────────────────────────────────────────

    cumulative: list[int] = []

    for turn in range(1, 11):

        # ── Untap / upkeep ───────────────────────────────────────────────────
        lands_in_play  += pending_lands
        pending_lands   = 0
        selvala_sick    = False   # clear sickness from last turn's entry
        sentinel_sick   = False

        # ── Draw step ────────────────────────────────────────────────────────
        draw_card("natural_draw")

        # Sylvan Library: draw 1 extra card per turn and pay 4 life to keep it.
        # (Optimal line in 40-life Commander: keep 1 extra indefinitely.)
        if sylvan_on:
            draw_card("sylvan_library")

        # ── Mana calculation ─────────────────────────────────────────────────
        mana = lands_in_play + ramp_mana

        # ── Between-turns effects (fired during opponents' turns) ─────────────
        # Esper Sentinel: across 3 opponents, each has ~60 % chance of casting
        # a noncreature spell without paying the sentinel tax.
        if sentinel_left and not sentinel_sick:
            triggers = sum(1 for _ in range(3) if rng.random() < 0.60)
            actual   = min(triggers, sentinel_left)
            for _ in range(actual):
                draw_card("esper_sentinel")
            sentinel_left -= actual
            if sentinel_left <= 0:
                sentinel_left = None   # sentinel destroyed / no longer relevant

        # Mind's Eye: each of 3 opponents draws in their draw step; costs {1} each.
        # We pay from open mana (the goldfish keeps mana up for this).
        if minds_eye_on:
            affordable = min(3, mana)
            for _ in range(affordable):
                draw_card("mind's_eye")
            mana -= affordable

        # ── Play a land ──────────────────────────────────────────────────────
        for card in hand:
            if is_land(card, card_data):
                hand.remove(card)
                lands_in_play += 1
                mana += 1
                break

        # ── Main phase: cast spells (strict priority order) ───────────────────

        # 1. Sol Ring (1 → +2/turn)
        if "Sol Ring" not in bf and try_cast("Sol Ring", 1):
            ramp_mana += 2
            mana      += 2   # tap immediately

        # 2. Birds of Paradise (1 → +1/turn next turn)
        if "Birds of Paradise" not in bf and try_cast("Birds of Paradise", 1):
            creatures.add("Birds of Paradise")
            ramp_mana += 1   # available from next turn (summoning sick now)

        # 3. Esper Sentinel (1)
        if sentinel_left is None and "Esper Sentinel" not in bf:
            if try_cast("Esper Sentinel", 1):
                creatures.add("Esper Sentinel")
                sentinel_sick = True
                # Sample total draws before removal; mean ≈ 2.5
                sentinel_left = rng.choices(
                    [1, 2, 3, 4, 5],
                    weights=[0.10, 0.25, 0.35, 0.20, 0.10],
                )[0]

        # 4. Skullclamp (1)
        if "Skullclamp" not in bf and try_cast("Skullclamp", 1):
            skullclamp_on = True

        # 5. Cheap land fetchers: Nature's Lore / Three Visits (2 → +1 untapped land)
        for fetch in ("Nature's Lore", "Three Visits"):
            if fetch in hand and mana >= 2:
                hand.remove(fetch)
                mana -= 2
                bf.add(fetch)
                lands_in_play += 1
                mana += 1
                break

        # 6. Farseek (2 → +1 tapped land next turn)
        if "Farseek" in hand and mana >= 2:
            hand.remove("Farseek")
            mana -= 2
            bf.add("Farseek")
            pending_lands += 1

        # 7. Arcane Signet (2 → +1/turn)
        if "Arcane Signet" not in bf and try_cast("Arcane Signet", 2):
            ramp_mana += 1
            mana      += 1

        # 8. Sylvan Library (2 → +1 draw/turn)
        if "Sylvan Library" not in bf and try_cast("Sylvan Library", 2):
            sylvan_on = True

        # 9. Faeburrow Elder (3 → +2/turn, summoning sick)
        if "Faeburrow Elder" not in bf and try_cast("Faeburrow Elder", 3):
            creatures.add("Faeburrow Elder")
            ramp_mana += 2   # available from next turn

        # 10. Selvala, Explorer Returned (3)
        if "Selvala, Explorer Returned" not in bf and try_cast("Selvala, Explorer Returned", 3):
            creatures.add("Selvala, Explorer Returned")
            selvala_on   = True
            selvala_sick = True

        # 11. Sword of Fire and Ice (3 to cast, 2 to equip)
        if "Sword of Fire and Ice" not in bf and try_cast("Sword of Fire and Ice", 3):
            sword_on = True
        if sword_on and not sword_equipped and mana >= 2 and creatures:
            mana -= 2
            sword_equipped = True

        # 12. Return of the Wildspeaker (3, one-shot sorcery)
        if "Return of the Wildspeaker" in hand and mana >= 3:
            hand.remove("Return of the Wildspeaker")
            mana -= 3
            # Draw X = greatest power among non-Human creatures you control.
            # Horses are typically 1–5 power; Crested Sunmare / tokens = 5.
            non_humans = creatures - HUMAN_CREATURES
            if non_humans:
                big_five = {"Crested Sunmare", "Thundering Mightmare",
                            "Shadowfax, Lord of Horses"}
                if non_humans & big_five:
                    max_power = 5
                elif len(non_humans) >= 3:
                    max_power = rng.randint(3, 4)
                else:
                    max_power = rng.randint(1, 3)
            else:
                max_power = 0
            for _ in range(max_power):
                draw_card("return_of_wildspeaker")

        # 13. Atla Palani (commander, base 4 mana; +2 per recast)
        if not atla_on_bf and mana >= atla_cost:
            mana -= atla_cost
            atla_on_bf = True
            atla_cost += 2
            bf.add("Atla Palani")
            creatures.add("Atla Palani")

        # 14. Mind's Eye (5)
        if "Mind's Eye" not in bf and try_cast("Mind's Eye", 5):
            minds_eye_on = True

        # 15. Horses (one per turn)
        for horse in HORSES:
            cmc = compute_cmc(horse, card_data)
            if horse not in bf and horse in hand and mana >= cmc:
                hand.remove(horse)
                mana -= cmc
                bf.add(horse)
                creatures.add(horse)
                break   # one horse per turn in goldfish

        # ── Activate Selvala ──────────────────────────────────────────────────
        # Parley tap: you draw 1 card (opponents also draw, but we track ours).
        if selvala_on and not selvala_sick:
            draw_card("selvala")

        # ── Egg engine: Atla {2}+T → egg, Skullclamp {1} → egg dies → draw 2 ──
        # Atla can only tap once per turn → at most 1 egg per turn from her ability.
        if atla_on_bf and skullclamp_on and mana >= EGG_COST + SKULLCLAMP_EQUIP:
            mana -= EGG_COST + SKULLCLAMP_EQUIP
            draw_card("skullclamp_eggs")
            draw_card("skullclamp_eggs")

        # ── Combat: Sword of Fire and Ice ─────────────────────────────────────
        # Equipped creature deals combat damage to a player → draw 1.
        # 75 % connection rate: blocked occasionally, but usually finds a target.
        if sword_equipped and creatures and turn >= 2:
            if rng.random() < 0.75:
                draw_card("sword_fai")

        # Snapshot cumulative draws for per-turn chart
        cumulative.append(sum(draws.values()))

    return dict(draws), cumulative


def _run_chunk(args):
    """Run a slice of simulations in a worker process; returns (draws_list, cumul_list)."""
    deck, card_data, seeds = args
    draws_out, cumul_out = [], []
    for s in seeds:
        d, c = simulate_game(deck, card_data, s)
        draws_out.append(d)
        cumul_out.append(c)
    return draws_out, cumul_out


# ── Aggregation & output ──────────────────────────────────────────────────────

def _pct(data: list, p: float) -> float:
    s = sorted(data)
    return s[max(0, min(int(len(s) * p), len(s) - 1))]


DISPLAY_ORDER = [
    "opening_hand",
    "natural_draw",
    "sylvan_library",
    "selvala",
    "esper_sentinel",
    "mind's_eye",
    "skullclamp_eggs",
    "sword_fai",
    "return_of_wildspeaker",
]


def simulate(deck_path, cards_path, trials, seed=None):
    deck      = load_deck(deck_path)
    card_data = load_cards(cards_path)

    assert len(deck) == 99, f"Expected 99-card deck, got {len(deck)}"

    base_rng = random.Random(seed) if seed is not None else random.Random()
    seeds = [base_rng.randint(0, 2**32 - 1) for _ in range(trials)]

    n_workers = multiprocessing.cpu_count()
    chunk_size = (trials + n_workers - 1) // n_workers
    chunks = [seeds[i:i + chunk_size] for i in range(0, trials, chunk_size)]

    t0 = time.time()
    print(f"Running {trials:,} simulations across {len(chunks)} workers ({n_workers} cores)...", flush=True)

    all_draws: list[dict] = []
    all_cumul: list[list] = []

    with concurrent.futures.ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [executor.submit(_run_chunk, (deck, card_data, chunk)) for chunk in chunks]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            chunk_draws, chunk_cumul = future.result()
            all_draws.extend(chunk_draws)
            all_cumul.extend(chunk_cumul)
            print(f"  Worker {i}/{len(chunks)} done ({len(chunk_draws):,} games)", flush=True)
    print()

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s\n")

    # ── Source table ──────────────────────────────────────────────────────────
    all_sources = {k for r in all_draws for k in r}
    sources = DISPLAY_ORDER + sorted(all_sources - set(DISPLAY_ORDER))

    W = 72
    print("=" * W)
    print(f"  Card draw analysis - Atla Palani 'Hatched Horses Fighting'")
    print(f"  {trials:,} goldfish games x 10 turns")
    print("=" * W)
    print(f"  {'Source':<28} {'Mean':>5}  {'+/-SD':>5}  "
          f"{'P25':>4} {'P50':>4} {'P75':>4} {'P95':>4}  {'Active':>6}")
    print(f"  {'-'*(W-2)}")

    grand = [sum(r.values()) for r in all_draws]

    for src in sources:
        vals  = [r.get(src, 0) for r in all_draws]
        m     = mean(vals)
        sd    = stdev(vals)
        p25   = _pct(vals, 0.25)
        p50   = _pct(vals, 0.50)
        p75   = _pct(vals, 0.75)
        p95   = _pct(vals, 0.95)
        act   = 100 * sum(1 for v in vals if v > 0) / trials
        print(f"  {src:<28} {m:>5.2f}  {sd:>5.2f}  "
              f"{p25:>4} {p50:>4} {p75:>4} {p95:>4}  {act:>5.1f}%")

    print(f"  {'-'*(W-2)}")
    m   = mean(grand)
    sd  = stdev(grand)
    p25 = _pct(grand, 0.25)
    p50 = _pct(grand, 0.50)
    p75 = _pct(grand, 0.75)
    p95 = _pct(grand, 0.95)
    print(f"  {'TOTAL by turn 10':<28} {m:>5.2f}  {sd:>5.2f}  "
          f"{p25:>4} {p50:>4} {p75:>4} {p95:>4}  100.0%")

    # ── Cumulative draw curve ─────────────────────────────────────────────────
    print()
    print("  Avg cumulative cards seen by turn (hand + all draws):")
    print(f"  {'Turn':<6} {'Cards':>6}")
    print(f"  {'-'*14}")
    for t in range(10):
        avg = mean(c[t] for c in all_cumul)
        print(f"  {t + 1:<6} {avg:>6.1f}")

    # ── Assumptions ───────────────────────────────────────────────────────────
    print()
    print("=" * W)
    print("  Assumptions:")
    print("  - No mulligans; Atla Palani in command zone (not in 99).")
    print("  - Esper Sentinel: total draws before removal ~ {1-5}, mean ~2.5.")
    print("    Each turn alive, ~60% chance per opponent of triggering (3 opps).")
    print("  - Mind's Eye: 3 opponent draw-step triggers/turn, each costs {1}.")
    print("  - Sword of Fire and Ice: 75% connection rate while equipped.")
    print("  - Selvala, Explorer Returned: 1 draw per tap; not sick after entry turn.")
    print("  - Skullclamp eggs: Atla taps once/turn -> 1 egg -> Skullclamp -> draw 2.")
    print("    Total cost: 3 mana (EGG_COST=2 + EQUIP=1).  Requires Atla + Clamp.")
    print("  - Return of the Wildspeaker: X = estimated max power of non-Humans.")
    print("  - Sylvan Library: keep 1 extra card per turn, pay 4 life.")
    print("=" * W)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Goldfish card-draw simulator for Atla Palani (HHF)."
    )
    parser.add_argument("--trials", type=int, default=100_000,
                        help="Number of simulated games (default: 100,000).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility.")
    parser.add_argument("--deck",  default="deck.txt")
    parser.add_argument("--cards", default="cards.json")
    args = parser.parse_args()

    simulate(
        deck_path=args.deck,
        cards_path=args.cards,
        trials=args.trials,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
