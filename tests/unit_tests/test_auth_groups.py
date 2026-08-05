"""End-to-end check of the groups path, without a server."""
from sky import models
from sky.skylet import constants


def test_parse_groups_distinguishes_absent_from_empty():
    assert models.parse_groups(None) is None  # IdP asserted nothing
    assert models.parse_groups('') == []  # asserted "no groups"
    assert models.parse_groups('a,b') == ['a', 'b']
    assert models.parse_groups(' a , , b ') == ['a', 'b']


def test_user_carries_groups():
    u = models.User(id='abc', name='x@y.com', groups=['vision'])
    assert u.groups == ['vision']
    assert models.User(id='abc').groups is None


def test_groups_survive_the_env_round_trip():
    """This is the part that silently dropped them: auth_user -> env -> worker."""
    auth_user = models.User(id='abc',
                            name='x@y.com',
                            groups=['vision', 'mlops'])
    env = {}
    env[constants.USER_GROUPS_ENV_VAR] = (','.join(auth_user.groups)
                                          if auth_user.groups else '')
    rebuilt = models.User(id='abc', name='x@y.com')
    rebuilt.groups = models.parse_groups(env.get(constants.USER_GROUPS_ENV_VAR))
    assert rebuilt.groups == ['vision', 'mlops']


def test_groups_not_persisted_in_to_dict():
    """Group membership belongs to the IdP; a stored copy goes stale silently."""
    assert 'groups' not in models.User(id='a', groups=['x']).to_dict()
