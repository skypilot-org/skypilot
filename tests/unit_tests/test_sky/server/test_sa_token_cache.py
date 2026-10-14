"""Tests for the service-account Bearer auth cache."""

# pylint: disable=protected-access,redefined-outer-name

import types
import unittest.mock as mock

import cachetools
import fastapi
import pytest

from sky import global_user_state
from sky.server import server
from sky.server.auth import sa_token_cache
from sky.skylet import constants


@pytest.fixture
def token_row():
    return {
        'token_id': 'token_db_id_456',
        'token_name': 'test-token',
        'token_hash': 'hash_xyz',
        'created_at': 1700000000,
        'last_used_at': None,
        'expires_at': None,
        'creator_user_hash': 'user-creator',
        'service_account_user_id': 'sa-123456',
    }


@pytest.fixture
def user_info():
    user = mock.Mock()
    user.name = 'test-service-account'
    return user


@pytest.fixture
def db(monkeypatch, token_row, user_info):
    """The DB calls the Bearer middleware makes, patched; return values can be
    overridden per test."""
    monkeypatch.setenv(constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS, 'true')
    calls = types.SimpleNamespace(
        by_hash=mock.Mock(return_value=token_row),
        get_user=mock.Mock(return_value=user_info),
        update_last_used=mock.Mock(),
    )
    monkeypatch.setattr(global_user_state, 'get_service_account_token_by_hash',
                        calls.by_hash)
    monkeypatch.setattr(global_user_state, 'get_user', calls.get_user)
    monkeypatch.setattr(global_user_state,
                        'update_service_account_token_last_used',
                        calls.update_last_used)
    with mock.patch('sky.users.token_service.token_service') as token_service:
        token_service.secret_loaded.return_value = True
        token_service.verify_token.return_value = {
            'sub': 'sa-123456',
            'name': 'test-service-account',
            'token_id': 'token_123',
        }
        yield calls


async def _call_next(request):
    del request
    return fastapi.responses.JSONResponse({'message': 'success'})


async def _dispatch():
    request = mock.Mock(spec=fastapi.Request)
    request.headers = {'authorization': 'Bearer sky_cached_token'}
    request.state = mock.Mock()
    request.state.auth_user = None
    middleware = server.BearerTokenMiddleware(app=mock.Mock())
    response = await middleware.dispatch(request, _call_next)
    return response, request


@pytest.mark.asyncio
async def test_hit_skips_db_lookups(db):
    for _ in range(3):
        response, request = await _dispatch()
        assert response.status_code == 200
        assert request.state.auth_user.id == 'sa-123456'
    assert db.by_hash.call_count == 1
    assert db.get_user.call_count == 1


@pytest.mark.asyncio
async def test_entry_expires_after_ttl(db, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(
        sa_token_cache, '_entries',
        cachetools.TTLCache(maxsize=8,
                            ttl=sa_token_cache.TTL_SECONDS,
                            timer=lambda: now[0]))
    await _dispatch()
    now[0] += sa_token_cache.TTL_SECONDS - 0.5
    await _dispatch()
    assert db.by_hash.call_count == 1
    now[0] += 1
    await _dispatch()
    assert db.by_hash.call_count == 2


@pytest.mark.asyncio
async def test_revoked_token_is_not_cached(db, token_row):
    db.by_hash.return_value = None
    response, _ = await _dispatch()
    assert response.status_code == 401
    db.by_hash.return_value = token_row
    response, _ = await _dispatch()
    assert response.status_code == 200
    assert db.by_hash.call_count == 2


@pytest.mark.asyncio
async def test_deleted_user_is_not_cached(db, user_info):
    db.get_user.return_value = None
    response, _ = await _dispatch()
    assert response.status_code == 401
    assert b'no longer exists' in response.body
    db.get_user.return_value = user_info
    response, _ = await _dispatch()
    assert response.status_code == 200
    assert db.get_user.call_count == 2


@pytest.mark.asyncio
async def test_cached_row_still_checks_expiry(db, token_row):
    response, _ = await _dispatch()
    assert response.status_code == 200
    # The cached row holds the same dict; it is now past its expiry.
    token_row['expires_at'] = 1700000001
    response, _ = await _dispatch()
    assert response.status_code == 401
    assert b'expired' in response.body
    assert db.by_hash.call_count == 1


@pytest.mark.asyncio
async def test_last_used_written_once_across_hits(db):
    for _ in range(5):
        await _dispatch()
    db.update_last_used.assert_called_once_with(
        'token_db_id_456', server._SA_LAST_USED_UPDATE_INTERVAL_SECONDS)


@pytest.mark.asyncio
async def test_zero_ttl_disables_cache(db, monkeypatch):
    monkeypatch.setattr(sa_token_cache, 'TTL_SECONDS', 0)
    await _dispatch()
    await _dispatch()
    assert db.by_hash.call_count == 2


@pytest.mark.parametrize('raw,expected', [
    ('0', 0.0),
    ('2.5', 2.5),
])
def test_ttl_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv(constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS, raw)
    assert sa_token_cache._get_ttl_seconds() == expected


def test_ttl_default(monkeypatch):
    monkeypatch.delenv(constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS,
                       raising=False)
    assert (sa_token_cache._get_ttl_seconds() ==
            constants.DEFAULT_SA_AUTH_CACHE_TTL_SECONDS)


@pytest.mark.parametrize('raw', ['-1', 'abc', 'inf', 'nan'])
def test_invalid_ttl_is_refused(monkeypatch, raw):
    monkeypatch.setenv(constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS, raw)
    with pytest.raises(ValueError,
                       match=constants.ENV_VAR_SA_AUTH_CACHE_TTL_SECONDS):
        sa_token_cache._get_ttl_seconds()
