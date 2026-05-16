"""Pitcher strikeout projection models.

v0: flat expected_BF, blended K% only.
v1: per-pitcher expected_BF, log5 matchup vs opposing team K%.
v2: SwStr-blended pitcher K%, lineup-level opp K%, park K factor.

Also exports market math (Poisson P(over), American odds conversions, EV).
"""

from __future__ import annotations

import math

from scipy.stats import poisson

from .config import (
    DEFAULT_EXPECTED_BF,
    LEAGUE_K_PCT,
    LEAGUE_SWSTR_PCT,
    PARK_K_FACTOR_DEFAULT,
    RECENT_FORM_WEIGHT,
    SWSTR_BLEND_WEIGHT,
)


def blended_k_rate(season_k_pct: float, recent_k_pct: float) -> float:
    """Blend season K% with recent-form K% per RECENT_FORM_WEIGHT.

    Falls back to whichever value is non-zero if one is missing.
    """
    if recent_k_pct <= 0:
        return season_k_pct
    if season_k_pct <= 0:
        return recent_k_pct
    return RECENT_FORM_WEIGHT * recent_k_pct + (1 - RECENT_FORM_WEIGHT) * season_k_pct


def expected_batters_faced(
    season_bf_per_start: float,
    recent_bf_per_start: float,
) -> float:
    """Blend season and recent BF/start the same way we blend K%.

    Falls back to DEFAULT_EXPECTED_BF if both inputs are missing.
    """
    if recent_bf_per_start <= 0 and season_bf_per_start <= 0:
        return float(DEFAULT_EXPECTED_BF)
    if recent_bf_per_start <= 0:
        return season_bf_per_start
    if season_bf_per_start <= 0:
        return recent_bf_per_start
    return (
        RECENT_FORM_WEIGHT * recent_bf_per_start
        + (1 - RECENT_FORM_WEIGHT) * season_bf_per_start
    )


def matchup_k_rate(
    pitcher_k_pct: float,
    opp_team_k_pct: float,
    league_k_pct: float = LEAGUE_K_PCT,
) -> float:
    """log5 matchup K rate: (pitcher_K% × opp_K%) / league_K%.

    Standard sabermetric formula for combining a pitcher's rate and an
    opponent's rate into a per-PA expected outcome rate.

    Falls back gracefully when one side is missing or league_K% is 0.
    """
    if league_k_pct <= 0:
        return pitcher_k_pct
    if opp_team_k_pct <= 0:
        return pitcher_k_pct
    if pitcher_k_pct <= 0:
        return opp_team_k_pct
    return (pitcher_k_pct * opp_team_k_pct) / league_k_pct


def project_pitcher_ks_v0(
    season_k_pct: float,
    recent_k_pct: float,
    expected_bf: int = DEFAULT_EXPECTED_BF,
) -> float:
    """v0 projection: blended K% × flat expected_BF (no matchup)."""
    return blended_k_rate(season_k_pct, recent_k_pct) * expected_bf


def project_pitcher_ks_v1(
    season_k_pct: float,
    recent_k_pct: float,
    opp_team_k_pct: float,
    season_bf_per_start: float,
    recent_bf_per_start: float,
    league_k_pct: float = LEAGUE_K_PCT,
) -> dict:
    """v1 projection: log5(blended pitcher K%, opp team K%) × per-pitcher expected BF.

    Returns a dict so callers can inspect intermediate values:
        blended_k_pct, matchup_k_pct, expected_bf, proj_ks
    """
    blended = blended_k_rate(season_k_pct, recent_k_pct)
    matchup = matchup_k_rate(blended, opp_team_k_pct, league_k_pct)
    bf = expected_batters_faced(season_bf_per_start, recent_bf_per_start)
    return {
        "blended_k_pct": blended,
        "matchup_k_pct": matchup,
        "expected_bf": bf,
        "proj_ks": matchup * bf,
    }


# ---------- v2 ----------


def expected_k_pct_from_swstr(swstr_pct: float) -> float:
    """Map a pitcher's SwStr% to an implied K% using the league ratio.

    A pitcher who whiffs 14% of pitches (vs 11.5% league) projects to
    K% ≈ 14% × (LEAGUE_K_PCT / LEAGUE_SWSTR_PCT) ≈ 27%.
    """
    if swstr_pct <= 0 or LEAGUE_SWSTR_PCT <= 0:
        return 0.0
    return swstr_pct * (LEAGUE_K_PCT / LEAGUE_SWSTR_PCT)


def blended_pitcher_k_with_swstr(
    actual_k_pct: float,
    swstr_pct: float,
) -> float:
    """Blend the pitcher's actual K% with the SwStr-implied K%.

    SwStr% stabilizes faster than K%, so weighting it pulls early-season
    outliers toward a more defensible projection. Falls back gracefully
    when one input is missing.
    """
    xk = expected_k_pct_from_swstr(swstr_pct)
    if xk <= 0:
        return actual_k_pct
    if actual_k_pct <= 0:
        return xk
    return SWSTR_BLEND_WEIGHT * xk + (1 - SWSTR_BLEND_WEIGHT) * actual_k_pct


def project_pitcher_ks_v2(
    season_k_pct: float,
    recent_k_pct: float,
    swstr_pct: float,
    opp_k_pct: float,
    season_bf_per_start: float,
    recent_bf_per_start: float,
    park_factor: float = PARK_K_FACTOR_DEFAULT,
    league_k_pct: float = LEAGUE_K_PCT,
) -> dict:
    """v2: SwStr-blended K%, lineup-or-team opp K%, park multiplier."""
    blended_actual = blended_k_rate(season_k_pct, recent_k_pct)
    pitcher_k = blended_pitcher_k_with_swstr(blended_actual, swstr_pct)
    matchup = matchup_k_rate(pitcher_k, opp_k_pct, league_k_pct) * park_factor
    bf = expected_batters_faced(season_bf_per_start, recent_bf_per_start)
    return {
        "blended_actual_k": blended_actual,
        "pitcher_k": pitcher_k,
        "matchup_k_pct": matchup,
        "expected_bf": bf,
        "proj_ks": matchup * bf,
    }


# ---------- Hitter strikeouts ----------


def project_hitter_ks_v0(
    season_k_pct: float,
    recent_k_pct: float,
    opp_pitcher_k_pct: float,
    expected_pa: float,
    park_factor: float = PARK_K_FACTOR_DEFAULT,
    league_k_pct: float = LEAGUE_K_PCT,
) -> dict:
    """Hitter K projection — log5 mirror of the pitcher path.

        matchup_k%  = (hitter_K% × pitcher_K% / league_K%) × park_factor
        proj_ks     = matchup_k% × expected_pa

    Treats the entire game's PAs as if they're vs the opposing starter — a
    deliberate simplification for v0. Bullpen K% blending is a v1 feature.

    Returns a dict so callers can inspect intermediate values.
    """
    blended = blended_k_rate(season_k_pct, recent_k_pct)
    matchup = matchup_k_rate(blended, opp_pitcher_k_pct, league_k_pct) * park_factor
    return {
        "blended_k_pct": blended,
        "matchup_k_pct": matchup,
        "expected_pa": expected_pa,
        "proj_ks": matchup * expected_pa,
    }


# ---------- Market math ----------


def prob_over_poisson(line: float, projected_ks: float) -> float:
    """P(K > line) assuming K count is Poisson(mean = projected_ks).

    Lines are conventionally X.5, so floor(line) is the over threshold.
    """
    if projected_ks <= 0:
        return 0.0
    threshold = math.floor(line)
    return float(poisson.sf(threshold, projected_ks))


def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal odds."""
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / abs(american)


def implied_prob(american: int) -> float:
    """Vig-included implied probability from American odds."""
    return 1 / american_to_decimal(american)


def novig_implied_probs(over_odds: int, under_odds: int) -> tuple[float, float]:
    """Strip the vig: return fair implied (P_over, P_under) summing to 1."""
    p_over = implied_prob(over_odds)
    p_under = implied_prob(under_odds)
    total = p_over + p_under
    if total <= 0:
        return 0.0, 0.0
    return p_over / total, p_under / total


def ev_per_dollar(american: int, true_prob: float) -> float:
    """Expected value per $1 wagered given a true win probability."""
    decimal = american_to_decimal(american)
    return true_prob * (decimal - 1) - (1 - true_prob)
