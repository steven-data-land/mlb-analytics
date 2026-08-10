"""Betting-edge math: American odds <-> implied probability, de-vigging,
and the naive-independent-vs-true-correlated joint probability comparison.

Pure functions, no I/O -- see README for the full methodology writeup.
"""


def american_to_implied_prob(odds: float) -> float:
    """Convert American odds (e.g. -150, +120) to implied probability.

    This includes the sportsbook's vig -- it's what the price implies, not
    a "fair" probability. De-vig with devig_pair() when both sides of a
    market are known.
    """
    if odds > 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def devig_pair(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Given both sides of a two-outcome market, strip the vig by
    normalizing the two raw implied probabilities to sum to 1."""
    raw_a = american_to_implied_prob(odds_a)
    raw_b = american_to_implied_prob(odds_b)
    total = raw_a + raw_b
    return raw_a / total, raw_b / total


def naive_independent_joint(p_win: float, p_strong_k: float) -> float:
    """What buying both legs as separate, uncorrelated contracts effectively
    costs you in probability terms (a prediction-market RFQ fill, or any
    pricing that doesn't account for the correlation between them)."""
    return p_win * p_strong_k


def true_correlated_joint(p_win: float, p_strong_k: float, lift_ratio: float) -> float:
    """The model's estimate of the actual joint probability, applying the
    pitcher's historical K-lift ratio to the market's own win probability
    (not our own historical win rate -- see README methodology) rather than
    assuming the two legs are independent.

    Capped at p_strong_k (the joint can't exceed either marginal) since
    lift_ratio can push p_win * lift_ratio above 1.0 for small-sample or
    already-heavily-favored pitchers.
    """
    lifted_p_win = min(p_win * lift_ratio, 1.0)
    return min(p_strong_k * lifted_p_win, p_strong_k)
