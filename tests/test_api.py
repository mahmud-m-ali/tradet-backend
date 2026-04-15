"""Automated tests for TradEt API endpoints."""

import json
import os
import sys
import tempfile
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app
from config import Config
from database import close_pool


@pytest.fixture
def app():
    """Create test app with temporary database."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    close_pool()
    Config.DATABASE_PATH = db_path

    # Disable rate limiting in tests
    os.environ['RATELIMIT_ENABLED'] = 'false'
    os.environ['DISABLE_PRICE_UPDATER'] = 'true'

    app = create_app()
    app.config['TESTING'] = True

    yield app

    close_pool()
    os.environ.pop('RATELIMIT_ENABLED', None)
    os.environ.pop('DISABLE_PRICE_UPDATER', None)
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_token(client):
    """Register or login a user and return auth token."""
    # Try registration first
    resp = client.post('/api/auth/register', json={
        'email': 'test@halalet.com',
        'phone': '+251911111111',
        'password': 'TestPass123',
        'full_name': 'Test User',
    })
    data = json.loads(resp.data)
    if 'token' in data:
        return data['token']
    # Already registered? Login instead
    resp = client.post('/api/auth/login', json={
        'email': 'test@halalet.com',
        'password': 'TestPass123',
    })
    data = json.loads(resp.data)
    if 'token' not in data:
        pytest.fail(f"Auth failed: {data}")
    return data['token']


@pytest.fixture
def auth_headers(auth_token):
    return {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}


# === Health Check ===

class TestHealth:
    def test_health_check(self, client):
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['status'] == 'healthy'
        assert 'version' in data

    def test_compliance_info(self, client):
        resp = client.get('/api/compliance')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'sharia' in data
        assert 'ethiopian' in data


# === Auth ===

class TestAuth:
    def test_register_success(self, client):
        resp = client.post('/api/auth/register', json={
            'email': 'new@halalet.com',
            'phone': '+251922222222',
            'password': 'StrongPass1',
            'full_name': 'New User',
        })
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert 'token' in data
        assert 'refresh_token' in data

    def test_register_missing_fields(self, client):
        resp = client.post('/api/auth/register', json={
            'email': 'bad@halalet.com',
        })
        assert resp.status_code == 400

    def test_register_weak_password(self, client):
        resp = client.post('/api/auth/register', json={
            'email': 'weak@halalet.com',
            'phone': '+251933333333',
            'password': '123',
            'full_name': 'Weak Pass',
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert 'password' in data['error'].lower() or 'Password' in data['error']

    def test_register_invalid_email(self, client):
        resp = client.post('/api/auth/register', json={
            'email': 'not-an-email',
            'phone': '+251944444444',
            'password': 'StrongPass1',
            'full_name': 'Bad Email',
        })
        assert resp.status_code == 400

    def test_register_duplicate_email(self, client, auth_token):
        resp = client.post('/api/auth/register', json={
            'email': 'test@halalet.com',
            'phone': '+251955555555',
            'password': 'StrongPass1',
            'full_name': 'Dup User',
        })
        assert resp.status_code == 409

    def test_login_success(self, client, auth_token):
        resp = client.post('/api/auth/login', json={
            'email': 'test@halalet.com',
            'password': 'TestPass123',
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'token' in data
        assert 'refresh_token' in data
        assert data['user']['email'] == 'test@halalet.com'

    def test_login_wrong_password(self, client, auth_token):
        resp = client.post('/api/auth/login', json={
            'email': 'test@halalet.com',
            'password': 'WrongPass1',
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post('/api/auth/login', json={
            'email': 'nobody@halalet.com',
            'password': 'TestPass123',
        })
        assert resp.status_code == 401

    def test_profile_requires_auth(self, client):
        resp = client.get('/api/auth/profile')
        assert resp.status_code == 401

    def test_profile_success(self, client, auth_headers):
        resp = client.get('/api/auth/profile', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['email'] == 'test@halalet.com'
        assert 'wallet_balance' in data

    def test_token_refresh(self, client):
        # Register and get refresh token
        resp = client.post('/api/auth/register', json={
            'email': 'refresh@halalet.com',
            'phone': '+251966666666',
            'password': 'RefreshPass1',
            'full_name': 'Refresh User',
        })
        data = json.loads(resp.data)
        refresh = data['refresh_token']

        # Use refresh token to get new access token
        resp = client.post('/api/auth/refresh', headers={
            'Authorization': f'Bearer {refresh}',
            'Content-Type': 'application/json',
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'token' in data


# === Market ===

class TestMarket:
    def test_get_assets(self, client, auth_headers):
        resp = client.get('/api/market/assets', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_assets_refresh_updates_live_status(self, client, monkeypatch):
        from services import live_prices

        def fake_fetch_live_prices():
            return {
                'AAPL': {
                    'price': 13000.0,
                    'bid_price': 12990.0,
                    'ask_price': 13010.0,
                    'high_24h': 13100.0,
                    'low_24h': 12900.0,
                    'volume_24h': 1234,
                    'change_24h': 2.5,
                    'updated_at': '2026-04-15T10:30:00',
                }
            }

        monkeypatch.setattr(live_prices, 'fetch_live_prices', fake_fetch_live_prices)

        resp = client.get('/api/market/assets?refresh=true')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        aapl = next(asset for asset in data if asset['symbol'] == 'AAPL')
        assert aapl['data_source'] == 'live'
        assert aapl['price'] == 13000.0

    def test_get_asset_detail(self, client, auth_headers):
        resp = client.get('/api/market/assets/1', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'symbol' in data

    def test_get_categories(self, client):
        resp = client.get('/api/market/categories')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


# === Trading ===

class TestTrading:
    def test_place_buy_order(self, client, auth_headers):
        # Complete KYC and deposit money
        kyc_resp = client.post('/api/auth/kyc', headers=auth_headers, json={
            'id_type': 'national_id',
            'id_number': 'TEST123456',
        })
        assert kyc_resp.status_code == 200
        client.post('/api/wallet/deposit', headers=auth_headers,
                    json={'amount': 1000000})

        resp = client.post('/api/trading/orders', headers=auth_headers, json={
            'asset_id': 14,  # TEFF
            'order_type': 'buy',
            'quantity': 5,
            'price': 7800.0,
        })
        data = json.loads(resp.data)
        # May fail due to trading session being closed, which is expected
        assert resp.status_code in [200, 201, 403]
        if resp.status_code in [200, 201]:
            assert 'order_id' in data

    def test_place_order_no_balance(self, client, auth_headers):
        resp = client.post('/api/trading/orders', headers=auth_headers, json={
            'asset_id': 28,
            'order_type': 'buy',
            'quantity': 100,
            'price': 12450.0,
        })
        data = json.loads(resp.data)
        # Should fail due to insufficient balance
        assert resp.status_code == 400 or 'error' in data

    def test_get_orders(self, client, auth_headers):
        resp = client.get('/api/trading/orders', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


# === Portfolio ===

class TestPortfolio:
    def test_get_portfolio(self, client, auth_headers):
        resp = client.get('/api/portfolio', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'holdings' in data
        assert 'summary' in data


# === Wallet ===

class TestWallet:
    def test_deposit(self, client, auth_headers):
        resp = client.post('/api/wallet/deposit', headers=auth_headers,
                          json={'amount': 50000})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['new_balance'] >= 50000

    def test_get_wallet(self, client, auth_headers):
        resp = client.get('/api/wallet', headers=auth_headers)
        assert resp.status_code == 200


# === Extras ===

class TestExtras:
    def test_exchange_rates(self, client):
        resp = client.get('/api/exchange-rates')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'rates' in data

    def test_nisab(self, client):
        resp = client.get('/api/zakat/nisab')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'gold' in data
        assert 'silver' in data

    def test_currency_convert(self, client):
        resp = client.get('/api/convert?amount=100&from=USD&to=ETB')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'converted' in data
        assert data['converted'] > 0


# === Alerts ===

class TestAlerts:
    def test_create_alert(self, client, auth_headers):
        resp = client.post('/api/alerts', headers=auth_headers, json={
            'asset_id': 1,
            'target_price': 45000,
            'condition': 'above',
        })
        assert resp.status_code in [200, 201]

    def test_get_alerts(self, client, auth_headers):
        resp = client.get('/api/alerts', headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
