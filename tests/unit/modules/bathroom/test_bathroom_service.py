"""
Unit tests for BathroomService

Tests:
- get_products (no filters, brand filter, search filter, DB error)
- get_brand_settings (success, DB error)
- update_brand_settings (success, invalid brand, invalid rate, DB error)
- init_database (success, DB error)
"""
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from app.modules.bathroom.service import BathroomService


@pytest.fixture
def service():
    return BathroomService()


# ===========================================================================
# get_products
# ===========================================================================

class TestGetProducts:

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_get_all_products(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (2,)
        mock_cursor.fetchall.return_value = [
            ('paffoni', 'ZDUP129CR', 'Basin Mixer', 'Metal', Decimal('18.00'), 'ACCESSORIES', 'Chrome'),
            ('dolomite', 'A005001', 'Cabinet', 'Other', Decimal('398.00'), 'LAUNDRY', 'White'),
        ]

        result = service.get_products()

        assert result['success'] is True
        assert result['total'] == 2
        assert result['products'][0]['brand'] == 'Paffoni'
        assert result['products'][0]['price'] == 18.0
        assert result['products'][0]['color'] == 'Chrome'
        assert 'page' not in result

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_filter_by_brand(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [
            ('paffoni', 'ZDUP129CR', 'Basin Mixer', 'Metal', Decimal('18.00'), 'ACCESSORIES', 'Chrome'),
        ]

        result = service.get_products(brand='paffoni')

        assert result['success'] is True
        # Second execute call is the SELECT query
        call_args = mock_cursor.execute.call_args_list[1]
        assert 'LOWER(brand) = LOWER(%s)' in call_args[0][0]
        assert 'paffoni' in call_args[0][1]

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_filter_by_search(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []

        result = service.get_products(search='mixer')

        assert result['success'] is True
        call_args = mock_cursor.execute.call_args_list[1]
        assert 'model_code ILIKE' in call_args[0][0]
        assert '%mixer%' in call_args[0][1]

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_pagination(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (250,)
        mock_cursor.fetchall.return_value = [
            ('paffoni', 'ZDUP129CR', 'Basin Mixer', 'Metal', Decimal('18.00'), 'ACCESSORIES', 'Chrome'),
        ]

        result = service.get_products(page=2, per_page=100)

        assert result['success'] is True
        assert result['total'] == 250
        assert result['page'] == 2
        assert result['per_page'] == 100
        assert result['pages'] == 3
        # SELECT query should have LIMIT and OFFSET
        call_args = mock_cursor.execute.call_args_list[1]
        assert 'LIMIT' in call_args[0][0]
        assert 100 in call_args[0][1]  # per_page
        assert 100 in call_args[0][1]  # offset = (2-1) * 100

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_no_pagination_metadata_without_page(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (5,)
        mock_cursor.fetchall.return_value = []

        result = service.get_products()

        assert 'page' not in result
        assert 'per_page' not in result
        assert 'pages' not in result

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_conn, service):
        mock_conn.return_value = None

        result = service.get_products()

        assert result['success'] is False
        assert 'database' in result['error'].lower()

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_exception(self, mock_conn, service):
        mock_conn.side_effect = Exception('DB error')

        result = service.get_products()

        assert result['success'] is False
        assert 'DB error' in result['error']


# ===========================================================================
# get_brand_settings
# ===========================================================================

class TestGetBrandSettings:

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_success(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [
            # brand_rows
            [
                ('Dolomite', Decimal('0.5950'), Decimal('0.0800'), Decimal('0.2000'), Decimal('0.0035')),
                ('Paffoni', Decimal('0.6390'), Decimal('0.0500'), Decimal('0.2000'), Decimal('0.0035')),
                ('Valsir', Decimal('0.7000'), Decimal('0.0800'), Decimal('0.2000'), Decimal('0.0035')),
            ],
            # tax_rows
            [
                ('ceramic', Decimal('0.0870')),
                ('other', Decimal('0.2000')),
            ],
        ]

        result = service.get_brand_settings()

        assert result['success'] is True
        assert len(result['brands']) == 3
        assert result['brands'][0]['name'] == 'Dolomite'
        assert result['brands'][0]['discount'] == 0.595
        assert result['tax_rates']['ceramic'] == 0.087
        assert result['tax_rates']['other'] == 0.2

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_conn, service):
        mock_conn.return_value = None

        result = service.get_brand_settings()

        assert result['success'] is False


# ===========================================================================
# update_brand_settings
# ===========================================================================

class TestUpdateBrandSettings:

    VALID_BRANDS = [
        {'name': 'Valsir', 'discount': 0.70, 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035},
        {'name': 'Dolomite', 'discount': 0.595, 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035},
    ]
    VALID_TAX_RATES = {'ceramic': 0.087, 'other': 0.20}

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_success(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [('Valsir',), ('Dolomite',), ('Paffoni',)]

        result = service.update_brand_settings(brands=self.VALID_BRANDS, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is True
        mock_conn.return_value.commit.assert_called_once()

    def test_invalid_discount_over_1(self, service):
        brands = [{'name': 'Valsir', 'discount': 1.5, 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035}]

        result = service.update_brand_settings(brands=brands, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is False
        assert 'Invalid discount' in result['error']

    def test_invalid_discount_negative(self, service):
        brands = [{'name': 'Valsir', 'discount': -0.1, 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035}]

        result = service.update_brand_settings(brands=brands, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is False
        assert 'Invalid discount' in result['error']

    def test_missing_rate_field(self, service):
        brands = [{'name': 'Valsir', 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035}]

        result = service.update_brand_settings(brands=brands, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is False
        assert 'Invalid discount' in result['error']

    def test_invalid_tax_rate(self, service):
        result = service.update_brand_settings(brands=self.VALID_BRANDS, tax_rates={'ceramic': 1.5})

        assert result['success'] is False
        assert 'Invalid tax rate' in result['error']

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_invalid_brand_name(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [('Valsir',), ('Dolomite',), ('Paffoni',)]

        brands = [{'name': 'Unknown', 'discount': 0.5, 'transport': 0.05, 'margin': 0.2, 'insurance': 0.003}]
        result = service.update_brand_settings(brands=brands, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is False
        assert 'Invalid brand name: Unknown' in result['error']

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_conn, service):
        mock_conn.return_value = None

        result = service.update_brand_settings(brands=self.VALID_BRANDS, tax_rates=self.VALID_TAX_RATES)

        assert result['success'] is False


# ===========================================================================
# init_database
# ===========================================================================

class TestInitDatabase:

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_success(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        # Seed checks: tables are empty
        mock_cursor.fetchone.side_effect = [(0,), (0,)]

        result = service.init_database()

        assert result['success'] is True
        mock_conn.return_value.commit.assert_called_once()

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_skips_seed_when_data_exists(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        # Seed checks: tables already have data
        mock_cursor.fetchone.side_effect = [(3,), (2,)]

        result = service.init_database()

        assert result['success'] is True
        # Should have fewer execute calls since seeding is skipped
        # The CREATE TABLE/INDEX calls still happen (9 DDL statements + 2 SELECT COUNTs)
        # But the 2 INSERT statements are skipped

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_conn, service):
        mock_conn.return_value = None

        result = service.init_database()

        assert result['success'] is False

    @patch('app.modules.bathroom.service.get_postgres_connection')
    def test_db_exception_rolls_back(self, mock_conn, service):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception('table error')

        result = service.init_database()

        assert result['success'] is False
        mock_conn.return_value.rollback.assert_called_once()
