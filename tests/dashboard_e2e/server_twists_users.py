"""Server twists for the Users page tests (see framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from framework.server_plugin import register_body_twist

# The cluster summary behind the per-user Clusters column comes back with
# only the caller's clusters, as if "all users" were ignored.
register_body_twist('srv-users-status-ignores-all-users', '/status', lambda b: {
    **b, 'all_users': False
})

# A role update keeps the user's old role: the server drops the requested
# role and only touches the password path.
register_body_twist('srv-users-update-drops-role', '/users/update',
                    lambda b: {k: v for k, v in b.items() if k != 'role'})

# A batch role change applies the default role instead of the requested one.
register_body_twist('srv-users-batch-update-wrong-role', '/users/batch_update',
                    lambda b: {
                        **b, 'role': 'user'
                    })

# Deleting a user targets an id that does not exist, so nothing is deleted.
register_body_twist(
    'srv-users-delete-wrong-id', '/users/delete', lambda b: {
        **b, 'user_id': f'{b.get("user_id", "")}-missing'
    })

# A new service account gets the default role instead of the chosen one.
register_body_twist('srv-users-sa-create-ignores-role',
                    '/users/service-account-tokens',
                    lambda b: {k: v for k, v in b.items() if k != 'role'})

# Rotating a service account token keeps its old expiry instead of the one
# chosen in the dialog.
register_body_twist(
    'srv-users-sa-rotate-ignores-expiry',
    '/users/service-account-tokens/rotate',
    lambda b: {k: v for k, v in b.items() if k != 'expires_in_days'})
