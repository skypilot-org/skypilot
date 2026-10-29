"""Users and service accounts for the Users page tests.

Basic-auth users are what the Users tab lists (service accounts live on their
own tab), so tests create them through the API the way an admin would, and
remove them afterwards. Expected totals come straight from the DB, never from
the endpoint the page reads.
"""
import hashlib
from typing import Any, Callable, Dict, Iterator, List

from framework import seed
import pytest
import requests

from sky import global_user_state
from sky.utils import common_utils

# The users the page drops before rendering (sky/dashboard data connector).
_HIDDEN_LEGACY_NAMES = ('dashboard', 'local')


def basic_user_id(username: str) -> str:
    """Basic users get a stable id derived from the name (users/create)."""
    return hashlib.md5(
        username.encode(),
        usedforsecurity=False).hexdigest()[:common_utils.USER_HASH_LENGTH]


def listed_users() -> List[Any]:
    """Users the Users tab lists for an admin: everyone except service
    accounts and the hidden legacy dashboard users."""
    out = []
    for user in global_user_state.get_all_users():
        if user.is_service_account():
            continue
        if user.name in _HIDDEN_LEGACY_NAMES and user.user_type == 'legacy':
            continue
        out.append(user)
    return out


def system_user_ids() -> List[str]:
    # pylint: disable=import-outside-toplevel
    from sky.users import server as users_server
    present = {u.id for u in global_user_state.get_all_users()}
    return [
        uid for uid in users_server._INTERNAL_USER_IDS  # pylint: disable=protected-access
        if uid in present
    ]


def service_account_tokens() -> List[Dict[str, Any]]:
    return global_user_state.get_all_service_account_tokens()


def user_role(admin_api: requests.Session, user_id: str) -> str:
    r = admin_api.get(
        f'{admin_api.base_url}/users',  # type: ignore[attr-defined]
        timeout=60)
    r.raise_for_status()
    for user in r.json():
        if user['id'] == user_id:
            return user['role']
    return ''


def user_exists(user_id: str) -> bool:
    return global_user_state.get_user(user_id) is not None


def delete_token_by_name(admin_api: requests.Session, name: str) -> None:
    for token in service_account_tokens():
        if token['token_name'] == name:
            admin_api.post(
                f'{admin_api.base_url}/users/service-account-tokens/delete',  # type: ignore[attr-defined]
                json={'token_id': token['token_id']},
                timeout=60)


@pytest.fixture
def make_basic_user(admin_api: requests.Session,
                    uid: str) -> Iterator[Callable[..., str]]:
    """Create a basic-auth user named ``<uid>-<label>``; returns its id. The
    user is removed afterwards unless the test already deleted it."""
    created: List[str] = []

    def make(label: str, role: str = 'user') -> str:
        name = f'{uid}-{label}'
        r = admin_api.post(
            f'{admin_api.base_url}/users/create',  # type: ignore[attr-defined]
            json={
                'username': name,
                'password': f'{uid}-pw',
                'role': role
            },
            timeout=60)
        assert r.status_code == 200, (
            f'creating user {name}: {r.status_code} {r.text}')
        user_id = basic_user_id(name)
        created.append(user_id)
        return user_id

    yield make
    for user_id in created:
        if user_exists(user_id):
            admin_api.post(
                f'{admin_api.base_url}/users/delete',  # type: ignore[attr-defined]
                json={'user_id': user_id},
                timeout=60)


def _leftover_users() -> List[str]:
    return [
        f'user {u.name}' for u in global_user_state.get_all_users()
        if seed.is_test_name(u.name)
    ]


def _leftover_tokens() -> List[str]:
    return [
        f'service account token {t["token_name"]}'
        for t in service_account_tokens()
        if seed.is_test_name(t['token_name'])
    ]


seed.register_leftover_check(_leftover_users)
seed.register_leftover_check(_leftover_tokens)
