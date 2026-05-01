"""Project-wide configuration constants."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"

# Number of recent games to use when computing recent-form K%.
RECENT_STARTS_WINDOW = 5

# Weight on recent K% vs season K% in the v0 blend.
# 0.0 = pure season, 1.0 = pure recent.
RECENT_FORM_WEIGHT = 0.4

# Fallback expected batters faced when per-pitcher data is sparse.
DEFAULT_EXPECTED_BF = 23

# League-average K% used as the denominator in the log5 matchup formula.
# MLB league K% has hovered in the .220–.230 range in recent seasons.
# Refresh annually or replace with a dynamic fetch later.
LEAGUE_K_PCT = 0.225

# League-average SwStr% (swinging strikes / total pitches). Used to scale
# pitcher SwStr% into an implied K%.
LEAGUE_SWSTR_PCT = 0.115

# How much to weight SwStr-implied K% vs. the pitcher's actual K%.
# Higher = trust SwStr more (better against early-season noise / regression
# candidates). Lower = lean on observed K% from this season.
SWSTR_BLEND_WEIGHT = 0.35

# Park K-rate factors (1.00 = neutral). Keyed by HOME team_id.
# Approximations from multi-year FanGraphs Guts! data — refine annually.
# Anything not listed defaults to PARK_K_FACTOR_DEFAULT.
PARK_K_FACTOR_DEFAULT = 1.00
PARK_K_FACTORS = {
    115: 0.92,  # Rockies (Coors) — thin air dampens breaking balls
    111: 0.96,  # Red Sox (Fenway)
    137: 0.98,  # Giants (Oracle)
    142: 1.01,  # Twins (Target)
    141: 1.01,  # Blue Jays (Rogers Centre)
    119: 1.02,  # Dodgers
    121: 1.02,  # Mets (Citi)
    135: 1.02,  # Padres (Petco)
    136: 1.03,  # Mariners (T-Mobile)
}

# ---------- Hitter strikeouts ----------

# Average plate appearances per game by batting-order slot (1 = leadoff).
# Multi-year MLB averages — leadoff hits ~4.6 PA, #9 hits ~3.8.
LINEUP_PA = {
    1: 4.65,
    2: 4.55,
    3: 4.45,
    4: 4.35,
    5: 4.25,
    6: 4.15,
    7: 4.05,
    8: 3.95,
    9: 3.85,
}
DEFAULT_LINEUP_PA = 4.20  # fallback when slot is unknown

# Fraction of a typical PA that comes against the starting pitcher.
# Modern starter usage averages ~5.5 IP / 22-23 BF on a ~37-39 BF game.
STARTER_BF_FRACTION = 0.60
