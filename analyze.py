import json
import random
import re
from collections import defaultdict

# Jetmir's color identity (W, R, G). Used for "any color in your commander's
# color identity" sources like Command Tower and Arcane Signet.
DECK_COLORS = frozenset({'W', 'R', 'G'})

# Used for generic mana requirements: any color (including colorless) can satisfy them.
ALL_MANA = frozenset({'W', 'U', 'B', 'R', 'G', 'C'})

_BASIC_SUBTYPE_COLOR = {
    'Forest': 'G',
    'Mountain': 'R',
    'Plains': 'W',
    'Island': 'U',
    'Swamp': 'B',
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_deck(path):
    """Parse deck.txt into a list of card names (one entry per copy).

    Expects lines in the format: '<count> <card name>'
    e.g. '1 Sol Ring' or '10 Forest'
    """
    deck = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = re.match(r"(\d+)\s+(.+)", line)
            if not match:
                continue
            count = int(match.group(1))
            name = match.group(2).strip()
            deck.extend([name] * count)
    return deck


def load_tags(path):
    """Parse tagged.json into a dict mapping card name → list of tags."""
    with open(path) as f:
        tag_list = json.load(f)
    card_tags = defaultdict(list)
    for entry in tag_list:
        tag = entry["tag"]
        for card in entry["cards"]:
            card_tags[card].append(tag)
    return dict(card_tags)


def draw_hand(deck, size=7):
    """Draw a random opening hand without replacement."""
    return random.sample(deck, size)


def load_cards(path):
    """Load cards.json into a dict mapping card name → card data."""
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Mana cost parsing
# ---------------------------------------------------------------------------

def parse_mana_cost(cost_str):
    """
    Parse a mana cost string into a structured dict.

    Returns None for lands (empty / null cost).
    Otherwise returns a dict whose keys are:
      'generic'       → int (the colourless numeric portion, e.g. 3 in {3}{G})
      'W','U','B','R','G','C'  → int (number of that pip required)
      ('G','R'), …    → int (hybrid pips, tuple of the two options sorted)

    Examples
      '{3}{G}{W}' → {'generic': 3, 'G': 1, 'W': 1}
      '{1}{R/G}'  → {'generic': 1, ('G','R'): 1}
      '{W}'       → {'generic': 0, 'W': 1}
    """
    if not cost_str:
        return None

    result = {'generic': 0}
    for token in re.findall(r'\{([^}]+)\}', cost_str):
        if token.isdigit():
            result['generic'] += int(token)
        elif token == 'X':
            pass  # X costs are not counted toward CMC here
        elif '/' in token:
            parts = token.split('/')
            if parts[0].isdigit():
                # Generic-hybrid (e.g. {2/W}): treat the generic option as 1 generic
                result['generic'] += 1
            else:
                key = tuple(sorted(parts))
                result[key] = result.get(key, 0) + 1
        elif token in ('W', 'U', 'B', 'R', 'G', 'C'):
            result[token] = result.get(token, 0) + 1

    return result


# ---------------------------------------------------------------------------
# Land color inference
# ---------------------------------------------------------------------------

def _land_colors(card_entry):
    """
    Return a frozenset of mana colors the land can produce, inferred from
    its oracle text and type line.

    Used only for Land-typed cards. Non-land mana sources (rocks, dorks,
    ramp spells) are handled separately via the 'mana' tag.
    """
    oracle    = card_entry.get('oracle_text', '') or ''
    type_line = card_entry.get('type_line',   '') or ''

    # Basic lands: color comes from the land subtype.
    if 'Basic Land' in type_line:
        for subtype, color in _BASIC_SUBTYPE_COLOR.items():
            if subtype in type_line:
                return frozenset({color})
        return frozenset()

    # "Add one mana of any color in your commander's color identity."
    # Covers: Command Tower, Arcane Signet (when treated as a land), etc.
    if "commander's color identity" in oracle:
        return DECK_COLORS

    # "Add one mana of any color that a land an opponent controls could produce."
    # Exotic Orchard — conservatively assume it matches our deck colors.
    if "opponent controls could produce" in oracle:
        return DECK_COLORS

    # Fetch lands: derive colors from what basics they can search for.
    # e.g. "Search your library for a Forest or Plains card" → {G, W}
    if 'Search your library for' in oracle:
        fetched = set()
        for subtype, color in _BASIC_SUBTYPE_COLOR.items():
            if subtype in oracle:
                fetched.add(color)
        if fetched:
            return frozenset(fetched)

    # Typed non-basic lands (shock lands, check lands, temples, triomes…):
    # first try parsing explicit "Add {X}" pips from oracle text.
    pips = set(re.findall(r'Add \{([WUBRG])\}', oracle))
    if pips:
        return frozenset(pips)

    # Fall back to land subtypes in the type line
    # e.g. "Land — Mountain Plains" → {R, W}
    subtype_colors = set()
    for subtype, color in _BASIC_SUBTYPE_COLOR.items():
        if subtype in type_line:
            subtype_colors.add(color)
    if subtype_colors:
        return frozenset(subtype_colors)

    return frozenset()
