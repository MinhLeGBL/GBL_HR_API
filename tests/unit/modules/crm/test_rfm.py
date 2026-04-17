"""
Unit tests for app.modules.crm.rfm — pure scoring + segmentation logic.

These tests have NO database dependency and run in milliseconds.
"""
import pytest

from app.modules.crm.rfm import (
    SEGMENTS,
    classify_segment,
    compute_frequency_score,
    compute_monetary_quintile_scores,
    compute_recency_score,
    compute_weighted_score,
    score_customers,
)


# ---------------------------------------------------------------------------
# compute_recency_score
# ---------------------------------------------------------------------------

class TestRecencyScore:

    @pytest.mark.parametrize('days, expected', [
        # Tier 5: ≤ 90
        (0, 5),
        (1, 5),
        (45, 5),
        (90, 5),
        # Tier 4: 91-180
        (91, 4),
        (135, 4),
        (180, 4),
        # Tier 3: 181-365
        (181, 3),
        (270, 3),
        (365, 3),
        # Tier 2: 366-540
        (366, 2),
        (450, 2),
        (540, 2),
        # Tier 1: > 540
        (541, 1),
        (1000, 1),
        (3650, 1),
    ])
    def test_recency_thresholds(self, days, expected):
        assert compute_recency_score(days) == expected

    def test_negative_days_raises(self):
        with pytest.raises(ValueError, match='days_since_last_purchase'):
            compute_recency_score(-1)


# ---------------------------------------------------------------------------
# compute_frequency_score
# ---------------------------------------------------------------------------

class TestFrequencyScore:

    @pytest.mark.parametrize('count, expected', [
        # Tier 1: exactly 1
        (1, 1),
        # Tier 2: exactly 2
        (2, 2),
        # Tier 3: 3-4
        (3, 3),
        (4, 3),
        # Tier 4: 5-9
        (5, 4),
        (7, 4),
        (9, 4),
        # Tier 5: ≥ 10
        (10, 5),
        (50, 5),
        (1000, 5),
    ])
    def test_frequency_thresholds(self, count, expected):
        assert compute_frequency_score(count) == expected

    def test_zero_purchases_raises(self):
        with pytest.raises(ValueError, match='purchase_count'):
            compute_frequency_score(0)

    def test_negative_raises(self):
        with pytest.raises(ValueError, match='purchase_count'):
            compute_frequency_score(-1)


# ---------------------------------------------------------------------------
# compute_monetary_quintile_scores
# ---------------------------------------------------------------------------

class TestMonetaryQuintile:

    def test_empty_list(self):
        assert compute_monetary_quintile_scores([]) == []

    def test_single_customer_gets_top_score(self):
        assert compute_monetary_quintile_scores([1000]) == [5]

    def test_five_customers_each_in_own_quintile(self):
        # Sorted DESC: [50, 40, 30, 20, 10]
        # Position 0 → 5, 1 → 4, 2 → 3, 3 → 2, 4 → 1
        spends = [10, 50, 30, 40, 20]
        scores = compute_monetary_quintile_scores(spends)
        # 10 (rank 4) → 1, 50 (rank 0) → 5, 30 (rank 2) → 3, 40 (rank 1) → 4, 20 (rank 3) → 2
        assert scores == [1, 5, 3, 4, 2]

    def test_ten_customers_pairs_per_quintile(self):
        # 10 customers → 2 per quintile
        spends = list(range(100, 0, -10))   # [100, 90, ..., 10]
        scores = compute_monetary_quintile_scores(spends)
        # ranks 0,1 → 5; 2,3 → 4; 4,5 → 3; 6,7 → 2; 8,9 → 1
        assert scores == [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]

    def test_preserves_input_order(self):
        spends = [100, 50, 75, 25]
        scores = compute_monetary_quintile_scores(spends)
        # Returned scores correspond to the customer at each input index
        assert len(scores) == len(spends)
        # Highest spender (100, idx 0) gets highest score
        assert scores[0] == max(scores)
        # Lowest spender (25, idx 3) gets lowest score
        assert scores[3] == min(scores)

    def test_ties_broken_by_original_order(self):
        # All same spend → first in input wins higher quintile
        spends = [100, 100, 100, 100, 100]
        scores = compute_monetary_quintile_scores(spends)
        # Tie-break by original index ascending → index 0 highest, 4 lowest
        assert scores == [5, 4, 3, 2, 1]

    def test_three_customers(self):
        # 3 customers with rank positions 0, 1, 2
        # quintile_position = (rank * 5) // 3 → 0, 1, 3
        # scores → 5 - 0, 5 - 1, 5 - 3 → [5, 4, 2]
        spends = [300, 200, 100]
        assert compute_monetary_quintile_scores(spends) == [5, 4, 2]

    def test_all_scores_in_valid_range(self):
        # Random-ish data — all scores must be 1-5
        spends = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 100, 1000, 5000]
        scores = compute_monetary_quintile_scores(spends)
        assert all(1 <= s <= 5 for s in scores)


# ---------------------------------------------------------------------------
# compute_weighted_score
# ---------------------------------------------------------------------------

class TestWeightedScore:

    def test_all_max(self):
        # 0.2*5 + 0.3*5 + 0.5*5 = 5.0
        assert compute_weighted_score(5, 5, 5) == 5.0

    def test_all_min(self):
        # 0.2*1 + 0.3*1 + 0.5*1 = 1.0
        assert compute_weighted_score(1, 1, 1) == 1.0

    def test_m_heavy_weighting(self):
        # M=5, R=1, F=1: 0.2 + 0.3 + 2.5 = 3.0
        # Confirms M dominates as expected for luxury
        assert compute_weighted_score(1, 1, 5) == 3.0
        # M=1, R=5, F=5: 1.0 + 1.5 + 0.5 = 3.0
        # Same total but driven by R+F instead
        assert compute_weighted_score(5, 5, 1) == 3.0

    def test_rounding_to_2dp(self):
        # 0.2*3 + 0.3*4 + 0.5*5 = 0.6 + 1.2 + 2.5 = 4.3
        assert compute_weighted_score(3, 4, 5) == 4.3

    def test_returns_float(self):
        assert isinstance(compute_weighted_score(3, 3, 3), float)


# ---------------------------------------------------------------------------
# classify_segment
# ---------------------------------------------------------------------------

class TestClassifySegment:

    def test_vic_top_tier(self):
        # R=5, F=5, M=5 → weighted=5.0
        assert classify_segment(5, 5, 5, 5.0) == 'VIC'
        # Boundary: R=4, F=4, M=4
        assert classify_segment(4, 4, 4, compute_weighted_score(4, 4, 4)) == 'VIC'

    def test_vic_takes_priority_over_loyalist(self):
        # R=5, F=5, M=5 also satisfies Loyalist — VIC wins by rule order
        assert classify_segment(5, 5, 5, 5.0) == 'VIC'

    def test_loyalist(self):
        # R=4, F=4, M=3 → weighted = 0.2*4 + 0.3*4 + 0.5*3 = 3.5
        assert classify_segment(4, 4, 3, 3.5) == 'Loyalist'
        # R=3, F=3, weighted=3.5 (M=4): 0.2*3+0.3*3+0.5*4 = 0.6+0.9+2.0 = 3.5
        assert classify_segment(3, 3, 4, 3.5) == 'Loyalist'

    def test_loyalist_below_threshold_falls_through(self):
        # R=3, F=3, M=2 → weighted = 0.6 + 0.9 + 1.0 = 2.5 (< 3.5)
        # Doesn't match Loyalist; falls through. R=3 F=3 M=2: matches Core (R≥3 F≥2 M≥2)
        assert classify_segment(3, 3, 2, 2.5) == 'Core'

    def test_emerging(self):
        # Recent (R≥4), low frequency (F≤2), high spend (M≥3)
        assert classify_segment(5, 1, 5, compute_weighted_score(5, 1, 5)) == 'Emerging'
        assert classify_segment(4, 2, 3, compute_weighted_score(4, 2, 3)) == 'Emerging'

    def test_prospect(self):
        # Recent (R≥4), exactly one purchase (F=1), low spend (M≤2)
        assert classify_segment(5, 1, 1, compute_weighted_score(5, 1, 1)) == 'Prospect'
        assert classify_segment(4, 1, 2, compute_weighted_score(4, 1, 2)) == 'Prospect'

    def test_dormant_via_frequency(self):
        # Was good then went quiet — R≤2 AND F≥3
        assert classify_segment(1, 5, 3, compute_weighted_score(1, 5, 3)) == 'Dormant'
        assert classify_segment(2, 3, 1, compute_weighted_score(2, 3, 1)) == 'Dormant'

    def test_dormant_via_monetary(self):
        # Was good then went quiet — R≤2 AND M≥3
        assert classify_segment(1, 1, 5, compute_weighted_score(1, 1, 5)) == 'Dormant'
        assert classify_segment(2, 2, 3, compute_weighted_score(2, 2, 3)) == 'Dormant'

    def test_core(self):
        # Engaged base — R≥3, F≥2, M≥2 (and not VIC/Loyalist)
        assert classify_segment(3, 2, 2, compute_weighted_score(3, 2, 2)) == 'Core'

    def test_lapsed_default(self):
        # Catch-all — long inactive, low value
        assert classify_segment(1, 1, 1, 1.0) == 'Lapsed'
        # R=2 F=1 M=2 — doesn't match any rule
        assert classify_segment(2, 1, 2, compute_weighted_score(2, 1, 2)) == 'Lapsed'

    def test_all_outputs_in_valid_segments(self):
        # Brute force across the 5x5x5 score space — every combo returns a known segment
        for r in range(1, 6):
            for f in range(1, 6):
                for m in range(1, 6):
                    w = compute_weighted_score(r, f, m)
                    seg = classify_segment(r, f, m, w)
                    assert seg in SEGMENTS, f'Invalid segment {seg!r} for R={r} F={f} M={m}'


# ---------------------------------------------------------------------------
# score_customers (end-to-end orchestration)
# ---------------------------------------------------------------------------

class TestScoreCustomers:

    def test_empty_input(self):
        assert score_customers([]) == []

    def test_basic_enrichment(self):
        raw = [
            {'customer_sid': 1, 'recency': 30, 'frequency': 12, 'monetary': 250_000_000},
            {'customer_sid': 2, 'recency': 200, 'frequency': 1, 'monetary': 5_000_000},
        ]
        result = score_customers(raw)

        assert len(result) == 2
        for cust in result:
            assert 'r_score' in cust
            assert 'f_score' in cust
            assert 'm_score' in cust
            assert 'weighted_score' in cust
            assert 'segment' in cust
            # Original keys preserved
            assert 'customer_sid' in cust

    def test_does_not_mutate_input(self):
        raw = [{'customer_sid': 1, 'recency': 30, 'frequency': 5, 'monetary': 100}]
        original = dict(raw[0])
        score_customers(raw)
        assert raw[0] == original   # untouched

    def test_extra_keys_passed_through(self):
        raw = [{
            'customer_sid': 1, 'recency': 30, 'frequency': 5, 'monetary': 100,
            'name': 'Test User', 'email': 'test@example.com',
        }]
        result = score_customers(raw)
        assert result[0]['name'] == 'Test User'
        assert result[0]['email'] == 'test@example.com'

    def test_top_spender_gets_m5(self):
        # Use 5+ customers so quintile partitioning produces both M5 and M1 buckets.
        raw = [
            {'customer_sid': 1, 'recency': 1, 'frequency': 1, 'monetary': 1},
            {'customer_sid': 2, 'recency': 1, 'frequency': 1, 'monetary': 100},
            {'customer_sid': 3, 'recency': 1, 'frequency': 1, 'monetary': 10_000},
            {'customer_sid': 4, 'recency': 1, 'frequency': 1, 'monetary': 100_000},
            {'customer_sid': 5, 'recency': 1, 'frequency': 1, 'monetary': 1_000_000},
        ]
        result = score_customers(raw)
        # Top spender (sid 5) → M5
        assert result[4]['m_score'] == 5
        # Bottom spender (sid 1) → M1
        assert result[0]['m_score'] == 1

    def test_realistic_vic_customer(self):
        # R=5 (recent), F=5 (12 purchases), M=5 (top spender)
        raw = [
            {'customer_sid': 1, 'recency': 30, 'frequency': 12, 'monetary': 500_000_000},
            # Filler customers so the VIC isn't alone in the quintile sort
            *[
                {'customer_sid': 100 + i, 'recency': 1, 'frequency': 1, 'monetary': 1000}
                for i in range(4)
            ],
        ]
        result = score_customers(raw)
        vic = next(c for c in result if c['customer_sid'] == 1)
        assert vic['r_score'] == 5
        assert vic['f_score'] == 5
        assert vic['m_score'] == 5
        assert vic['segment'] == 'VIC'

    def test_realistic_lapsed_customer(self):
        # R=1 (>540 days), F=1, low monetary
        raw = [
            {'customer_sid': 1, 'recency': 700, 'frequency': 1, 'monetary': 100},
            *[
                {'customer_sid': 100 + i, 'recency': 1, 'frequency': 1, 'monetary': 10_000_000}
                for i in range(4)
            ],
        ]
        result = score_customers(raw)
        lapsed = next(c for c in result if c['customer_sid'] == 1)
        assert lapsed['r_score'] == 1
        assert lapsed['f_score'] == 1
        assert lapsed['m_score'] == 1
        assert lapsed['segment'] == 'Lapsed'
