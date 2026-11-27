"""Unit tests for sky.users.resolver."""
from sky import models
from sky.users import resolver


def test_every_entry_for_a_user_resolves_back_to_that_user():
    users = [
        models.User(id='c0ffee00', name='alice'),
        # Named after alice's id: an entry 'c0ffee00' must mean alice.
        models.User(id='b0b0b0b0', name='c0ffee00'),
        models.User(id='d1d1d1d1', name='dup'),
        models.User(id='d2d2d2d2', name='dup'),
        models.User(id='e0e0e0e0', name=None),
    ]
    res = resolver.UserResolver(users, admin_user_ids=[])
    for user in users:
        assert res.resolve_allowed_entries([res.preferred_entry(user.id)
                                           ]) == [user.id], user
        for entry in res.entries_for(user.id):
            assert res.resolve_allowed_entries([entry]) == [user.id], entry
    assert res.preferred_entry('c0ffee00') == 'alice'
    assert res.entries_for('b0b0b0b0') == ['b0b0b0b0']
