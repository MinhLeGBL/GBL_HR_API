"""
Pure RFM scoring + segmentation logic for luxury fashion retail (CR #40).

No DB, no I/O — all functions take primitives or lists and return primitives.
This makes the logic fully unit-testable without mocks.

Methodology (luxury hybrid approach):
- Recency:  fixed thresholds (luxury purchase cycles run 90-540+ days)
- Frequency: fixed thresholds (luxury is low-frequency by nature)
- Monetary:  quintile-based (handles long-tail spend skew)
- Weighting: 0.2R + 0.3F + 0.5M (M-heavy for luxury)
- Segments:  7 luxury segments, first-match-wins rule order
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Recency: days since last purchase → score (5=most recent)
RECENCY_THRESHOLDS = [
    (90,  5),   # ≤ 90 days  (≤ 3 months)
    (180, 4),   # 91-180     (3-6 months)
    (365, 3),   # 181-365    (6-12 months)
    (540, 2),   # 366-540    (12-18 months)
    # > 540 → 1
]

# Frequency: distinct purchase count → score (5=most frequent)
FREQUENCY_THRESHOLDS = [
    (1,  1),    # exactly 1
    (2,  2),    # exactly 2
    (4,  3),    # 3-4
    (9,  4),    # 5-9
    # ≥ 10 → 5
]

# Weighted score coefficients (sum = 1.0) — CR #45: rebalanced from 0.2/0.3/0.5
W_RECENCY   = 0.3
W_FREQUENCY = 0.3
W_MONETARY  = 0.4

# Engagement score coefficients (sum = 1.0) — R-heavy for outreach prioritization
E_RECENCY   = 0.5
E_FREQUENCY = 0.3
E_MONETARY  = 0.2

# 7 luxury segments — must match CR exactly
SEGMENTS = ['VIC', 'Loyalist', 'Emerging', 'Prospect', 'Dormant', 'Core', 'Lapsed']


# ---------------------------------------------------------------------------
# Per-customer scoring
# ---------------------------------------------------------------------------

def compute_recency_score(days_since_last_purchase: int) -> int:
    """
    Map recency in days to a 1-5 score using fixed luxury thresholds.

    Args:
        days_since_last_purchase: Non-negative integer.

    Returns:
        Score in [1, 5]. Higher = more recent.

    Raises:
        ValueError: If input is negative.
    """
    if days_since_last_purchase < 0:
        raise ValueError(f'days_since_last_purchase must be >= 0, got {days_since_last_purchase}')
    for threshold, score in RECENCY_THRESHOLDS:
        if days_since_last_purchase <= threshold:
            return score
    return 1


def compute_frequency_score(purchase_count: int) -> int:
    """
    Map distinct-purchase count to a 1-5 score using fixed luxury thresholds.

    Args:
        purchase_count: Number of distinct invoices/orders. Must be >= 1
            (customers with zero purchases should be excluded upstream).

    Returns:
        Score in [1, 5]. Higher = more frequent.

    Raises:
        ValueError: If input is < 1.
    """
    if purchase_count < 1:
        raise ValueError(f'purchase_count must be >= 1, got {purchase_count}')
    for threshold, score in FREQUENCY_THRESHOLDS:
        if purchase_count <= threshold:
            return score
    return 5


def compute_monetary_quintile_scores(spends: Sequence[int]) -> List[int]:
    """
    Assign a 1-5 monetary score to each customer based on quintile rank.

    Sorts customers by spend descending, then partitions into 5 buckets of
    equal size (top 20% → M5, ..., bottom 20% → M1). Ties at quintile
    boundaries are broken by original list order — slightly arbitrary but
    deterministic and matches pandas.qcut(rank='first') behavior.

    Args:
        spends: Sequence of total-spend values (non-negative ints), one per
            customer, in the order the caller wants scores returned.

    Returns:
        List of M scores (1-5) in the SAME ORDER as `spends`.

    Edge cases:
        - Empty input → empty list
        - Single customer → [5] (alone in top quintile)
        - Fewer than 5 customers → all customers ranked, each in their own
          quintile-equivalent (e.g., 3 customers → top spend gets 5, mid gets 3, low gets 1)
    """
    n = len(spends)
    if n == 0:
        return []
    if n == 1:
        return [5]

    # Pair each spend with its original index, sort by spend DESC (ties: original order)
    indexed = sorted(enumerate(spends), key=lambda x: (-x[1], x[0]))

    scores = [0] * n
    for rank, (orig_idx, _spend) in enumerate(indexed):
        # rank 0 is highest spender. Map rank to quintile (1-5):
        #   top 20% → 5, next 20% → 4, ..., bottom 20% → 1
        # Use ceiling-style bucketing so each bucket is roughly n/5.
        quintile_position = (rank * 5) // n   # 0..4
        scores[orig_idx] = 5 - quintile_position

    return scores


def compute_weighted_score(r_score: int, f_score: int, m_score: int,
                           weights: Optional[Dict[str, float]] = None) -> float:
    """
    Combine R/F/M scores using configurable weights.

    Args:
        weights: Optional dict with keys 'w_recency', 'w_frequency', 'w_monetary'.
                 Falls back to module-level defaults if not provided.

    Returns:
        Weighted score in [1.0, 5.0], rounded to 2 decimal places.
    """
    wr = weights['w_recency'] if weights else W_RECENCY
    wf = weights['w_frequency'] if weights else W_FREQUENCY
    wm = weights['w_monetary'] if weights else W_MONETARY
    return round(wr * r_score + wf * f_score + wm * m_score, 2)


def compute_engagement_score(r_score: int, f_score: int, m_score: int,
                              weights: Optional[Dict[str, float]] = None) -> float:
    """
    Recency-heavy engagement score for outreach prioritization (CR #45).

    Args:
        weights: Optional dict with keys 'e_recency', 'e_frequency', 'e_monetary'.
                 Falls back to module-level defaults if not provided.

    Returns:
        Engagement score in [1.0, 5.0], rounded to 2 decimal places.
    """
    er = weights['e_recency'] if weights else E_RECENCY
    ef = weights['e_frequency'] if weights else E_FREQUENCY
    em = weights['e_monetary'] if weights else E_MONETARY
    return round(er * r_score + ef * f_score + em * m_score, 2)


# ---------------------------------------------------------------------------
# Segment classification (CR #40 rules — first match wins)
# ---------------------------------------------------------------------------

def classify_segment(r_score: int, f_score: int, m_score: int, weighted_score: float) -> str:
    """
    Classify a customer into one of 7 luxury segments.

    Rules from CR #40 — applied in order, first match wins:
        VIC      : R≥4 AND F≥4 AND M≥4
        Loyalist : R≥3 AND F≥3 AND weighted ≥ 3.5
        Emerging : R≥4 AND F≤2 AND M≥3
        Prospect : R≥4 AND F=1 AND M≤2
        Dormant  : R≤2 AND (F≥3 OR M≥3)
        Core     : R≥3 AND F≥2 AND M≥2
        Lapsed   : (default — everything else)

    Returns:
        Segment name (one of SEGMENTS).
    """
    if r_score >= 4 and f_score >= 4 and m_score >= 4:
        return 'VIC'
    if r_score >= 3 and f_score >= 3 and weighted_score >= 3.5:
        return 'Loyalist'
    if r_score >= 4 and f_score <= 2 and m_score >= 3:
        return 'Emerging'
    if r_score >= 4 and f_score == 1 and m_score <= 2:
        return 'Prospect'
    if r_score <= 2 and (f_score >= 3 or m_score >= 3):
        return 'Dormant'
    if r_score >= 3 and f_score >= 2 and m_score >= 2:
        return 'Core'
    return 'Lapsed'


# ---------------------------------------------------------------------------
# End-to-end batch scorer
# ---------------------------------------------------------------------------

def score_customers(raw_customers: List[Dict],
                     weights: Optional[Dict[str, float]] = None) -> List[Dict]:
    """
    Apply full RFM scoring to a list of customers.

    Args:
        raw_customers: List of dicts, each must contain:
            - customer_sid (int)
            - recency (int, days since last purchase)
            - frequency (int, distinct purchase count, >= 1)
            - monetary (int, total spend in VND)
            (Other keys are passed through unchanged.)
        weights: Optional config dict from crm_config table. If provided,
            used for weighted_score and engagement_score computation.
            Falls back to module-level defaults if None.

    Returns:
        Same list of dicts, each enriched with:
            - r_score (int 1-5)
            - f_score (int 1-5)
            - m_score (int 1-5)
            - weighted_score (float, 2 decimals)
            - engagement_score (float, 2 decimals)
            - segment (str, one of SEGMENTS)

        The original list is not mutated; new dicts are returned.

    Raises:
        ValueError: If any customer has invalid recency/frequency.
    """
    if not raw_customers:
        return []

    # Quintile M-scoring requires the full population at once
    spends = [c['monetary'] for c in raw_customers]
    m_scores = compute_monetary_quintile_scores(spends)

    out: List[Dict] = []
    for cust, m_score in zip(raw_customers, m_scores):
        r_score = compute_recency_score(cust['recency'])
        f_score = compute_frequency_score(cust['frequency'])
        weighted = compute_weighted_score(r_score, f_score, m_score, weights)
        engagement = compute_engagement_score(r_score, f_score, m_score, weights)
        segment  = classify_segment(r_score, f_score, m_score, weighted)

        new_cust = {**cust,
                    'r_score': r_score,
                    'f_score': f_score,
                    'm_score': m_score,
                    'weighted_score': weighted,
                    'engagement_score': engagement,
                    'segment': segment}
        out.append(new_cust)

    return out
