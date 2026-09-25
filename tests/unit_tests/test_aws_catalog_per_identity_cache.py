"""The AWS catalog cache is keyed by the identity the catalog was built for.

A single API server process can serve more than one identity: workspaces may
each carry their own ``aws.profile``, and the server reuses executor processes
across requests. Before this, ``_user_df`` was one module global created "at
most once per a process' lifetime", so the first identity in a process fixed
that process's catalog for every identity after it.

Every test below uses TWO identities. A single-identity test proves nothing
here: the unkeyed cache returns the right answer for the first identity whether
or not a key exists, and it is every later identity that is served the wrong
catalog.
"""
import pytest

from sky.catalog import aws_catalog


@pytest.fixture()
def two_identities(monkeypatch):
    """Patch the catalog to a fake fetch, and make the identity switchable."""
    monkeypatch.setattr(aws_catalog, '_user_dfs', {})
    monkeypatch.setattr(aws_catalog, '_default_df', 'DEFAULT')

    who = {'hash': 'aaaaaaaa'}
    built = []

    def fake_fetch(df, aws_user_hash):
        built.append(aws_user_hash)
        return f'catalog-for-{aws_user_hash}'

    monkeypatch.setattr(aws_catalog, '_resolve_aws_user_hash',
                        lambda: who['hash'])
    monkeypatch.setattr(aws_catalog, '_fetch_and_apply_az_mapping', fake_fetch)
    return who, built


def test_each_identity_gets_its_own_catalog(two_identities):
    who, built = two_identities

    assert aws_catalog._get_df() == 'catalog-for-aaaaaaaa'
    who['hash'] = 'bbbbbbbb'
    assert aws_catalog._get_df() == 'catalog-for-bbbbbbbb', (
        'the second identity was served the first identity\'s catalog: its '
        'region set, and account-specific zone names that are not its own')
    who['hash'] = 'aaaaaaaa'
    assert aws_catalog._get_df() == 'catalog-for-aaaaaaaa'
    assert built == [
        'aaaaaaaa', 'bbbbbbbb'
    ], (f'expected one fetch per identity and reuse thereafter, got {built}')


def test_the_catalog_is_built_for_the_identity_it_is_cached_under(
        two_identities):
    """The key and the fetch come from one resolution, so they cannot differ.

    The hash used to be resolved inside the fetch, where the caller could not
    observe it -- and the fallback in ``_resolve_aws_user_hash`` means the
    answer is not always the active identity, so caching under the caller's
    idea of "who am I" was not sound.
    """
    who, built = two_identities

    for identity in ('aaaaaaaa', 'bbbbbbbb'):
        who['hash'] = identity
        assert aws_catalog._get_df() == f'catalog-for-{identity}'
    assert set(aws_catalog._user_dfs) == {'aaaaaaaa', 'bbbbbbbb'}
    for key, value in aws_catalog._user_dfs.items():
        assert value == f'catalog-for-{key}', (key, value)


def test_a_degraded_default_is_not_cached_for_an_identity(
        two_identities, monkeypatch):
    """``use_default_catalog_if_failed`` degrades one call, not the process.

    Caching the default under the identity would keep serving the degraded
    answer for the lifetime of the process, long after the fetch would have
    succeeded.
    """
    who, _ = two_identities

    def boom(df, aws_user_hash):
        raise RuntimeError('availability zone fetch failed')

    monkeypatch.setattr(aws_catalog, '_fetch_and_apply_az_mapping', boom)
    monkeypatch.setattr(aws_catalog.config, 'get_use_default_catalog_if_failed',
                        lambda: True)

    assert aws_catalog._get_df() == 'DEFAULT'
    assert aws_catalog._user_dfs == {}, aws_catalog._user_dfs


def test_a_fetch_failure_still_raises_when_the_default_is_not_permitted(
        two_identities, monkeypatch):
    who, _ = two_identities

    def boom(df, aws_user_hash):
        raise RuntimeError('availability zone fetch failed')

    monkeypatch.setattr(aws_catalog, '_fetch_and_apply_az_mapping', boom)
    monkeypatch.setattr(aws_catalog.config, 'get_use_default_catalog_if_failed',
                        lambda: False)

    with pytest.raises(RuntimeError):
        aws_catalog._get_df()
    assert aws_catalog._user_dfs == {}


def test_an_unreadable_identity_still_resolves_to_a_concrete_key(
        monkeypatch, tmp_path):
    """The existing fallback is preserved, and it keys the cache like any other.

    When the identity cannot be read, ``_resolve_aws_user_hash`` falls back to
    the newest ``az_mappings-*.csv`` on disk, else ``default``. That is a
    concrete hash, so it caches under itself rather than under whoever asked.
    """
    from sky import exceptions
    from sky.adaptors import aws as aws_adaptor
    del aws_adaptor  # only imported to make the patch target obvious

    def no_identity():
        raise exceptions.CloudUserIdentityError('no credentials')

    monkeypatch.setattr(aws_catalog.aws.AWS, 'get_active_user_identity',
                        staticmethod(no_identity))
    monkeypatch.setattr(aws_catalog.common, 'get_catalog_path',
                        lambda name: str(tmp_path / name.replace('/', '_')))

    assert aws_catalog._resolve_aws_user_hash() == 'default'
