"""
Unit tests for app.modules.crm.service.CRMService.

Mocks CRMRepository — tests business logic (formatting, gap-filling, pivoting)
without touching any database.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.crm.rfm import SEGMENTS
from app.modules.crm.service import CRMService


@pytest.fixture
def service():
    svc = CRMService()
    svc.repo = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# _ensure_computed → 503 when empty
# ---------------------------------------------------------------------------

class TestEnsureComputed:

    def test_returns_error_when_empty(self, service):
        service.repo.count_scored_customers.return_value = 0
        result = service.get_customers()
        assert result['success'] is False
        assert 'not yet computed' in result['error']

    def test_passes_when_data_exists(self, service):
        service.repo.count_scored_customers.return_value = 100
        service.repo.list_customer_scores.return_value = []
        result = service.get_customers()
        assert result['success'] is True


# ---------------------------------------------------------------------------
# get_customers
# ---------------------------------------------------------------------------

class TestGetCustomers:

    def test_formats_response(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.list_customer_scores.return_value = [{
            'customer_sid': 123, 'name': 'Nguyen Minh', 'email': None,
            'phone': '0909363636', 'recency': 45, 'frequency': 12,
            'monetary': 250_000_000, 'r_score': 5, 'f_score': 5, 'm_score': 5,
            'weighted_score': 5.0, 'engagement_score': 5.0, 'segment': 'VIC',
            'top_brand': 'AKRIS', 'top_category': 'WOMEN', 'category_breadth': 6,
            'last_purchase_date': date(2026, 3, 2),
        }]

        result = service.get_customers()
        assert result['success'] is True
        assert result['total'] == 1
        c = result['customers'][0]
        assert c['id'] == 123
        assert c['last_purchase'] == '2026-03-02'
        assert c['email'] == ''  # None → empty string
        assert c['weighted_score'] == 5.0
        assert c['engagement_score'] == 5.0

    def test_passes_filters_to_repo(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.list_customer_scores.return_value = []
        service.get_customers(segment='VIC', min_weighted_score=4.0)
        service.repo.list_customer_scores.assert_called_once_with(
            segment='VIC', min_weighted_score=4.0,
        )

    def test_null_last_purchase_date(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.list_customer_scores.return_value = [{
            'customer_sid': 1, 'name': 'X', 'email': None, 'phone': None,
            'recency': 0, 'frequency': 1, 'monetary': 0,
            'r_score': 5, 'f_score': 1, 'm_score': 1,
            'weighted_score': 1.8, 'engagement_score': 3.0, 'segment': 'Prospect',
            'top_brand': None, 'top_category': None,
            'category_breadth': 0, 'last_purchase_date': None,
        }]
        c = service.get_customers()['customers'][0]
        assert c['last_purchase'] is None
        assert c['top_brand'] == ''


# ---------------------------------------------------------------------------
# get_segments_summary
# ---------------------------------------------------------------------------

class TestGetSegmentsSummary:

    def test_fills_all_7_segments(self, service):
        service.repo.count_scored_customers.return_value = 100
        service.repo.aggregate_segment_summary.return_value = [
            {'segment': 'VIC', 'count': 10, 'revenue': 1_000_000,
             'avg_recency': 30.0, 'avg_frequency': 10.0, 'avg_monetary': 100_000},
        ]
        result = service.get_segments_summary()
        assert result['success'] is True
        assert len(result['segments']) == 7
        # VIC has data
        vic = next(s for s in result['segments'] if s['segment'] == 'VIC')
        assert vic['count'] == 10
        assert vic['percentage'] == 100.0  # only segment with data
        # Others are zero-filled
        lapsed = next(s for s in result['segments'] if s['segment'] == 'Lapsed')
        assert lapsed['count'] == 0
        assert lapsed['percentage'] == 0

    def test_preserves_segment_order(self, service):
        service.repo.count_scored_customers.return_value = 100
        service.repo.aggregate_segment_summary.return_value = []
        result = service.get_segments_summary()
        returned_order = [s['segment'] for s in result['segments']]
        assert returned_order == list(SEGMENTS)


# ---------------------------------------------------------------------------
# get_segments_trends
# ---------------------------------------------------------------------------

class TestGetSegmentsTrends:

    def test_pivots_into_wide_format(self, service):
        service.repo.fetch_segment_snapshots.return_value = [
            {'snapshot_month': date(2026, 3, 1), 'segment': 'VIC', 'customer_count': 35},
            {'snapshot_month': date(2026, 3, 1), 'segment': 'Lapsed', 'customer_count': 75},
            {'snapshot_month': date(2026, 4, 1), 'segment': 'VIC', 'customer_count': 38},
        ]
        result = service.get_segments_trends(months=12)
        assert result['success'] is True
        assert len(result['trends']) == 2

        march = result['trends'][0]
        assert march['month'] == '2026-03'
        assert march['VIC'] == 35
        assert march['Lapsed'] == 75
        assert march['Core'] == 0  # not in snapshot → zero

        april = result['trends'][1]
        assert april['VIC'] == 38

    def test_empty_snapshots(self, service):
        service.repo.fetch_segment_snapshots.return_value = []
        result = service.get_segments_trends()
        assert result == {'success': True, 'trends': []}


# ---------------------------------------------------------------------------
# get_heatmap
# ---------------------------------------------------------------------------

class TestGetHeatmap:

    def test_returns_all_25_cells(self, service):
        service.repo.count_scored_customers.return_value = 100
        service.repo.aggregate_rf_heatmap.return_value = [
            {'r': 5, 'f': 5, 'count': 28, 'total_monetary': 7_000_000_000},
        ]
        result = service.get_heatmap()
        assert result['success'] is True
        assert len(result['cells']) == 25

        cell_55 = next(c for c in result['cells'] if c['r'] == 5 and c['f'] == 5)
        assert cell_55['count'] == 28
        assert cell_55['total_monetary'] == 7_000_000_000

        # All other cells are zero-filled
        cell_11 = next(c for c in result['cells'] if c['r'] == 1 and c['f'] == 1)
        assert cell_11['count'] == 0
        assert cell_11['total_monetary'] == 0

    def test_503_when_not_computed(self, service):
        service.repo.count_scored_customers.return_value = 0
        result = service.get_heatmap()
        assert result['success'] is False


# ---------------------------------------------------------------------------
# get_config / update_config
# ---------------------------------------------------------------------------

class TestConfig:

    def test_get_config(self, service):
        service.repo.get_config.return_value = {
            'w_recency': 0.3, 'w_frequency': 0.3, 'w_monetary': 0.4,
            'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
        }
        result = service.get_config()
        assert result['success'] is True
        assert result['config']['w_recency'] == 0.3

    def test_update_config_valid(self, service):
        config = {
            'w_recency': 0.2, 'w_frequency': 0.3, 'w_monetary': 0.5,
            'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
        }
        result = service.update_config(config)
        assert result['success'] is True
        service.repo.update_config.assert_called_once_with(config)

    def test_update_config_weighted_sum_not_1(self, service):
        config = {
            'w_recency': 0.5, 'w_frequency': 0.3, 'w_monetary': 0.5,  # sum = 1.3
            'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'sum to 1.0' in result['error']

    def test_update_config_engagement_sum_not_1(self, service):
        config = {
            'w_recency': 0.3, 'w_frequency': 0.3, 'w_monetary': 0.4,
            'e_recency': 0.1, 'e_frequency': 0.1, 'e_monetary': 0.1,  # sum = 0.3
        }
        result = service.update_config(config)
        assert result['success'] is False

    def test_update_config_negative_value(self, service):
        config = {
            'w_recency': -0.1, 'w_frequency': 0.6, 'w_monetary': 0.5,
            'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'between 0 and 1' in result['error']
