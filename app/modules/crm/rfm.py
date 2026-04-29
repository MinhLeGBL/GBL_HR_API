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

import math
from typing import Dict, List, Optional, Sequence, Tuple


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


def compute_quintile_scores(values: Sequence[float], ascending: bool = False) -> List[int]:
    """
    Assign a 1-5 quintile score to each value, in input order.

    Args:
        values: Numeric values to bucket.
        ascending: When False (default), highest values → 5 (use for spend,
            frequency). When True, lowest values → 5 (use for recency days,
            where "fewer days since purchase" means more recent).

    Returns:
        List of scores (1-5) in the same order as `values`. Empty input → [].

    Tie-breaking: stable by original index, matching pandas.qcut(rank='first').
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [5]

    sign = 1 if ascending else -1
    indexed = sorted(enumerate(values), key=lambda x: (sign * x[1], x[0]))

    scores = [0] * n
    for rank, (orig_idx, _v) in enumerate(indexed):
        quintile_position = (rank * 5) // n   # 0..4
        scores[orig_idx] = 5 - quintile_position
    return scores


def compute_monetary_quintile_scores(spends: Sequence[int]) -> List[int]:
    """
    Assign a 1-5 monetary score to each customer based on quintile rank.

    Higher spend → higher score. See ``compute_quintile_scores`` for the
    bucketing semantics — this is the descending-sort case.
    """
    return compute_quintile_scores(spends, ascending=False)


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


# ---------------------------------------------------------------------------
# Product (brand/category) scoring within a segment (CR #48)
# ---------------------------------------------------------------------------

def pool_product_aggregates_by_segment(per_customer_rows: List[Dict],
                                         customer_segments: Dict[int, str]
                                         ) -> Dict[str, List[Dict]]:
    """
    Combine per-(customer, brand|category) rows into per-segment aggregates
    using **nested per-customer averaging** (CR #49).

    For each (segment, brand) pair, every customer who bought that brand
    contributes a single per-customer value per metric; the result is the
    arithmetic mean across those customers. This treats each customer as one
    data point — a single power buyer no longer drowns out the segment's
    typical relationship with the brand.

    Args:
        per_customer_rows: List of dicts from ``fetch_product_aggregates``,
            each with customer_sid, group_name, items, revenue,
            customer_recency_days.
        customer_segments: Dict mapping customer_sid → segment name. Rows for
            customers absent from this map are skipped.

    Returns:
        Dict keyed by segment, each value a list of
        {name, recency_days, frequency, monetary} ready for scoring.
        ``frequency`` and ``monetary`` are means rounded to 2 decimals and
        whole VND respectively.
    """
    pooled: Dict[str, Dict[str, List[Tuple[int, int, int]]]] = {}
    for r in per_customer_rows:
        seg = customer_segments.get(r['customer_sid'])
        if seg is None:
            continue
        name = r['group_name']
        if name is None:
            continue
        seg_bucket = pooled.setdefault(seg, {})
        seg_bucket.setdefault(name, []).append((
            r['customer_recency_days'],
            r['items'],
            r['revenue'],
        ))

    out: Dict[str, List[Dict]] = {}
    for seg, names in pooled.items():
        rows = []
        for name, values in names.items():
            n = len(values)
            mean_recency  = sum(v[0] for v in values) / n
            mean_items    = sum(v[1] for v in values) / n
            mean_revenue  = sum(v[2] for v in values) / n
            rows.append({
                'name':           name,
                'customer_count': n,
                'recency_days':   round(mean_recency, 2),
                'frequency':      round(mean_items, 2),
                'monetary':       round(mean_revenue),
            })
        out[seg] = rows
    return out


def score_product_groups(groups: List[Dict],
                          weights: Optional[Dict[str, float]] = None) -> List[Dict]:
    """
    Apply quintile RFM scoring to a list of product (brand or category)
    aggregates within a single segment, plus a compound (intensity × breadth)
    score per CR #51.

    Args:
        groups: List of dicts, each with:
            - name (str)
            - customer_count (int; number of customers in segment that
              bought the brand/category — used for c_score and compound_score)
            - recency_days (float; mean across customers in the segment)
            - frequency (float; mean items per customer)
            - monetary (int; mean revenue per customer, VND)
            (Other keys pass through.)
        weights: Optional dict with 'w_recency'/'w_frequency'/'w_monetary'.

    Returns:
        Same list enriched with r_score, f_score, m_score, weighted_score
        (per-customer RFM intensity), c_score (quintile of customer_count),
        and compound_score (geometric mean of weighted × c, range 1.0-5.0),
        sorted by compound_score DESC (ties broken by name ASC).
    """
    if not groups:
        return []

    recency_vals  = [g['recency_days']   for g in groups]
    freq_vals     = [g['frequency']      for g in groups]
    monetary_vals = [g['monetary']       for g in groups]
    count_vals    = [g['customer_count'] for g in groups]

    r_scores = compute_quintile_scores(recency_vals,  ascending=True)
    f_scores = compute_quintile_scores(freq_vals,     ascending=False)
    m_scores = compute_quintile_scores(monetary_vals, ascending=False)
    c_scores = compute_quintile_scores(count_vals,    ascending=False)

    scored = []
    for g, rs, fs, ms, cs in zip(groups, r_scores, f_scores, m_scores, c_scores):
        weighted = compute_weighted_score(rs, fs, ms, weights)
        compound = round(math.sqrt(weighted * cs), 2)
        scored.append({**g,
                       'r_score':        rs,
                       'f_score':        fs,
                       'm_score':        ms,
                       'weighted_score': weighted,
                       'c_score':        cs,
                       'compound_score': compound})
    scored.sort(key=lambda x: (-x['compound_score'], x['name']))
    return scored
