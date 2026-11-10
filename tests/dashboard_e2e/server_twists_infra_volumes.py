"""Server twists for the Infra and Volumes pages (see
framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from framework.server_plugin import register_body_twist

# Refresh on the Infra page runs a full `sky check`. Here the server narrows
# it to one infra type, so the Kubernetes verdict it caches is never
# re-evaluated and a context that went away stays listed.
register_body_twist('srv-infra_volumes-check-narrowed-to-ssh', '/check',
                    lambda b: {
                        **b, 'clouds': ['ssh']
                    })

# The server drops the purge flag, so "Force remove" behaves like a plain
# delete: the cloud-side deletion fails again and the record stays.
register_body_twist('srv-infra_volumes-delete-drops-purge', '/volumes/delete',
                    lambda b: {
                        **b, 'purge': False
                    })
