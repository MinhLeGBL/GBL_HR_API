"""
Unit tests for app.modules.crm.service.CRMService.

Mocks CRMRepository — tests business logic (formatting, gap-filling, pivoting)
without touching any database.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.crm.rfm import SEGMENTS
from app.modules.crm.service import CRMService, _season_prefix


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
        # CR #55: SIDs are 18-digit BIGINTs in production. Use one here so the
        # serialization assertion is meaningful for JS clients.
        sid_18 = 690837303000121462
        service.repo.list_customer_scores.return_value = [{
            'customer_sid': sid_18, 'name': 'Nguyen Minh', 'email': None,
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
        # CR #55: id must be a string to preserve precision past Number.MAX_SAFE_INTEGER
        assert c['id'] == '690837303000121462'
        assert isinstance(c['id'], str)
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
            'w_recency': 0.3,  'w_frequency': 0.3,  'w_monetary': 0.4,
            'e_recency': 0.5,  'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': 0.3, 'pw_frequency': 0.3, 'pw_monetary': 0.4,
        }
        result = service.get_config()
        assert result['success'] is True
        assert result['config']['w_recency'] == 0.3
        assert result['config']['pw_recency'] == 0.3

    def test_update_config_valid(self, service):
        config = {
            'w_recency': 0.2,  'w_frequency': 0.3,  'w_monetary': 0.5,
            'e_recency': 0.5,  'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': 0.4, 'pw_frequency': 0.3, 'pw_monetary': 0.3,
        }
        result = service.update_config(config)
        assert result['success'] is True
        service.repo.update_config.assert_called_once_with(config)

    def test_update_config_weighted_sum_not_1(self, service):
        config = {
            'w_recency': 0.5,  'w_frequency': 0.3,  'w_monetary': 0.5,  # sum = 1.3
            'e_recency': 0.5,  'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': 0.3, 'pw_frequency': 0.3, 'pw_monetary': 0.4,
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'sum to 1.0' in result['error']

    def test_update_config_engagement_sum_not_1(self, service):
        config = {
            'w_recency': 0.3,  'w_frequency': 0.3,  'w_monetary': 0.4,
            'e_recency': 0.1,  'e_frequency': 0.1,  'e_monetary': 0.1,  # sum = 0.3
            'pw_recency': 0.3, 'pw_frequency': 0.3, 'pw_monetary': 0.4,
        }
        result = service.update_config(config)
        assert result['success'] is False

    def test_update_config_product_sum_not_1(self, service):
        # CR #52: pw_* must also sum to 1.0
        config = {
            'w_recency': 0.3,  'w_frequency': 0.3,  'w_monetary': 0.4,
            'e_recency': 0.5,  'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': 0.5, 'pw_frequency': 0.5, 'pw_monetary': 0.5,  # sum = 1.5
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'Product-analysis' in result['error']

    def test_update_config_negative_value(self, service):
        config = {
            'w_recency': -0.1, 'w_frequency': 0.6,  'w_monetary': 0.5,
            'e_recency': 0.5,  'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': 0.3, 'pw_frequency': 0.3, 'pw_monetary': 0.4,
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'between 0 and 1' in result['error']

    def test_update_config_pw_negative_value(self, service):
        # CR #52: pw_* values must also be in [0, 1]
        config = {
            'w_recency': 0.3,   'w_frequency': 0.3,  'w_monetary': 0.4,
            'e_recency': 0.5,   'e_frequency': 0.3,  'e_monetary': 0.2,
            'pw_recency': -0.5, 'pw_frequency': 0.7, 'pw_monetary': 0.8,  # sum 1.0 but pw_recency is negative
        }
        result = service.update_config(config)
        assert result['success'] is False
        assert 'pw_recency' in result['error']


# ---------------------------------------------------------------------------
# get_product_analysis (CR #48)
# ---------------------------------------------------------------------------

class TestGetProductAnalysis:

    def test_503_when_not_computed(self, service):
        service.repo.count_product_scores.return_value = 0
        result = service.get_product_analysis(group_by='brand')
        assert result['success'] is False
        assert 'not yet computed' in result['error']

    def test_groups_rows_by_segment(self, service):
        service.repo.count_product_scores.return_value = 4
        service.repo.list_product_scores.return_value = [
            {'segment': 'VIC', 'name': 'GUCCI', 'customer_count': 23,
             'recency_days': 12.5, 'frequency': 50, 'monetary': 100_000_000,
             'r_score': 5, 'f_score': 5, 'm_score': 5,
             'weighted_score': 5.0, 'c_score': 5, 'compound_score': 5.0},
            {'segment': 'VIC', 'name': 'PRADA', 'customer_count': 15,
             'recency_days': 30.0, 'frequency': 25, 'monetary': 40_000_000,
             'r_score': 4, 'f_score': 4, 'm_score': 4,
             'weighted_score': 4.0, 'c_score': 4, 'compound_score': 4.0},
            {'segment': 'Loyalist', 'name': 'GUCCI', 'customer_count': 8,
             'recency_days': 60.0, 'frequency': 12, 'monetary': 8_000_000,
             'r_score': 5, 'f_score': 4, 'm_score': 3,
             'weighted_score': 4.0, 'c_score': 5, 'compound_score': 4.47},
        ]
        result = service.get_product_analysis(group_by='brand')
        assert result['success'] is True
        # All 7 segments must be keys (empty list when no data)
        assert set(result['groups'].keys()) == set(SEGMENTS)
        assert len(result['groups']['VIC']) == 2
        assert len(result['groups']['Loyalist']) == 1
        assert len(result['groups']['Lapsed']) == 0
        # Row contents formatted with proper types
        vic_row = result['groups']['VIC'][0]
        assert vic_row['name'] == 'GUCCI'
        assert vic_row['customer_count'] == 23           # CR #50
        assert isinstance(vic_row['customer_count'], int)
        assert vic_row['frequency'] == 50
        assert isinstance(vic_row['frequency'], float)   # CR #49: fractional
        assert isinstance(vic_row['monetary'], int)
        assert isinstance(vic_row['recency_days'], float)
        assert vic_row['c_score'] == 5                    # CR #51
        assert vic_row['compound_score'] == 5.0           # CR #51
        assert isinstance(vic_row['compound_score'], float)

    def test_segment_filter_returns_only_that_segment(self, service):
        service.repo.count_product_scores.return_value = 1
        service.repo.list_product_scores.return_value = [
            {'segment': 'VIC', 'name': 'GUCCI', 'customer_count': 23,
             'recency_days': 12.5, 'frequency': 50, 'monetary': 100_000_000,
             'r_score': 5, 'f_score': 5, 'm_score': 5,
             'weighted_score': 5.0, 'c_score': 5, 'compound_score': 5.0},
        ]
        result = service.get_product_analysis(group_by='brand', segment='VIC')
        assert result['success'] is True
        assert list(result['groups'].keys()) == ['VIC']
        service.repo.list_product_scores.assert_called_once_with(
            group_by='brand', segment='VIC',
        )


# ---------------------------------------------------------------------------
# _season_prefix helper (CR #54)
# ---------------------------------------------------------------------------

class TestSeasonPrefix:

    @pytest.mark.parametrize('raw, expected', [
        ('SS25', 'SS'),
        ('FW24', 'FW'),
        ('AW23', 'AW'),
        ('RE25', 'RE'),
        ('ss25', 'SS'),       # uppercase normalisation
        ('  FW24  ', 'FW'),   # trims whitespace
        ('XYZ', 'XYZ'),       # all letters, no digits — passes through
        ('', 'Unknown'),
        (None, 'Unknown'),
        ('25', 'Unknown'),    # no leading letters
    ])
    def test_extracts_alpha_prefix(self, raw, expected):
        assert _season_prefix(raw) == expected


# ---------------------------------------------------------------------------
# get_customer_drilldown (CR #53)
# ---------------------------------------------------------------------------

class TestGetCustomerDrilldown:

    def test_503_when_not_computed(self, service):
        service.repo.count_scored_customers.return_value = 0
        result = service.get_customer_drilldown(123)
        assert result['success'] is False
        assert 'not yet computed' in result['error']

    def test_404_when_customer_has_no_transactions(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.fetch_customer_drilldown.return_value = {
            'totals': None, 'brands': [], 'categories': [],
        }
        result = service.get_customer_drilldown(123)
        assert result['success'] is False
        assert 'no transactions' in result['error']

    def test_assembles_response(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.fetch_customer_drilldown.return_value = {
            'totals': {
                'total_monetary':     12_345_678,
                'total_bills':        23,
                'fp_revenue':         8_000_000,
                'discounted_revenue': 4_345_678,
            },
            'brands': [
                {'brand_name': 'GUCCI', 'revenue': 5_000_000, 'brand_recency_days': 10},
                {'brand_name': 'PRADA', 'revenue': 3_000_000, 'brand_recency_days': 50},
            ],
            'categories': [
                {'category_name': 'BAG', 'revenue': 4_000_000},
                {'category_name': 'SHOES', 'revenue': 2_000_000},
            ],
        }
        result = service.get_customer_drilldown(123)
        assert result['success'] is True
        d = result['drilldown']
        assert d['total_monetary'] == 12_345_678
        assert d['total_bills'] == 23
        # avg_brand_recency_days = mean(10, 50) = 30.0
        assert d['avg_brand_recency_days'] == 30.0
        # Sorted by monetary desc
        assert d['brand_distribution'][0]['name'] == 'GUCCI'
        assert d['brand_distribution'][0]['monetary'] == 5_000_000
        assert d['category_distribution'][0]['name'] == 'BAG'
        assert d['price_distribution'] == {
            'full_price': 8_000_000, 'discounted': 4_345_678,
        }


# ---------------------------------------------------------------------------
# get_brand_drilldown (CR #54)
# ---------------------------------------------------------------------------

class TestGetBrandDrilldown:

    def test_503_when_not_computed(self, service):
        service.repo.count_scored_customers.return_value = 0
        result = service.get_brand_drilldown('GUCCI', segment='VIC')
        assert result['success'] is False
        assert 'not yet computed' in result['error']

    def test_invalid_segment(self, service):
        service.repo.count_scored_customers.return_value = 1
        result = service.get_brand_drilldown('GUCCI', segment='Whales')
        assert result['success'] is False
        assert result['error'].startswith('Invalid segment')

    def test_404_when_no_transactions(self, service):
        service.repo.count_scored_customers.return_value = 1
        service.repo.list_customer_sids_in_segment.return_value = [1, 2]
        service.repo.fetch_brand_drilldown.return_value = {
            'totals': [], 'seasons': [], 'categories': [],
        }
        result = service.get_brand_drilldown('GHOST', segment='VIC')
        assert result['success'] is False
        assert 'not found' in result['error']

    def test_splits_segment_vs_all(self, service):
        # Segment has customers 1 and 2 only. Customer 3 contributes only to all-segments.
        service.repo.count_scored_customers.return_value = 1
        service.repo.list_customer_sids_in_segment.return_value = [1, 2]
        service.repo.fetch_brand_drilldown.return_value = {
            'totals': [
                {'customer_sid': 1, 'revenue': 100, 'items': 5},
                {'customer_sid': 2, 'revenue': 200, 'items': 3},
                {'customer_sid': 3, 'revenue': 700, 'items': 12},  # outside segment
            ],
            'seasons': [
                {'customer_sid': 1, 'raw_season': 'SS25', 'revenue': 60},
                {'customer_sid': 1, 'raw_season': 'FW24', 'revenue': 40},
                {'customer_sid': 2, 'raw_season': 'SS25', 'revenue': 200},
                {'customer_sid': 3, 'raw_season': 'SS25', 'revenue': 500},
                {'customer_sid': 3, 'raw_season': None,   'revenue': 200},
            ],
            'categories': [
                {'customer_sid': 1, 'category_name': 'BAG',   'revenue': 100},
                {'customer_sid': 2, 'category_name': 'SHOES', 'revenue': 200},
                {'customer_sid': 3, 'category_name': 'BAG',   'revenue': 700},
            ],
        }

        result = service.get_brand_drilldown('GUCCI', segment='VIC')
        d = result['drilldown']

        # Headline metrics
        assert d['revenue_in_segment'] == 300         # 100 + 200
        assert d['revenue_all_segments'] == 1_000     # 100 + 200 + 700
        assert d['items_in_segment'] == 8             # 5 + 3 (customer 3 excluded)

        # Season distribution: customer 3's None → "Unknown"
        seg_seasons = {s['name']: s['monetary'] for s in d['season_distribution_segment']}
        all_seasons = {s['name']: s['monetary'] for s in d['season_distribution_all']}
        assert seg_seasons == {'SS': 260, 'FW': 40}
        assert all_seasons == {'SS': 760, 'FW': 40, 'Unknown': 200}
        # Sorted by monetary desc
        assert d['season_distribution_all'][0]['name'] == 'SS'

        # Category distribution
        seg_cats = {c['name']: c['monetary'] for c in d['category_distribution_segment']}
        all_cats = {c['name']: c['monetary'] for c in d['category_distribution_all']}
        assert seg_cats == {'BAG': 100, 'SHOES': 200}
        assert all_cats == {'BAG': 800, 'SHOES': 200}
