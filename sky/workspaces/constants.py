"""Workspace protocol constants.

Source codes returned alongside a resolved workspace (and surfaced by
``GET /users/me/workspace``). Kept in their own leaf module — separate
from :mod:`sky.workspaces.core` — so client-side callers (CLI, SDK
docs, dashboard helpers) can compare against them without dragging in
the heavy server-side resolver dependencies (DB, RBAC, backend_utils).
"""

# Sources reported by `resolve_workspace_for_user()` on the success
# path. Used by the launch flow and logged so users / operators can
# trace why a particular workspace was picked.
WORKSPACE_SOURCE_EXPLICIT = 'explicit'
WORKSPACE_SOURCE_PREFERRED = 'preferred'
# Preserves today's behavior for users (and admins) who can access the
# 'default' workspace and have not set a preference — they continue
# landing on 'default' instead of being surprised by an AMBIGUOUS error
# after upgrade.
WORKSPACE_SOURCE_DEFAULT_FALLBACK = 'default-fallback'
WORKSPACE_SOURCE_SINGLE_MEMBERSHIP = 'single-membership'

# Resolver-error states surfaced through `GET /users/me/workspace`. Not
# returned by `resolve_workspace_for_user()` itself (that path raises) —
# the handler writes one of these into the payload so callers (CLI /
# dashboard) can render a state code instead of parsing a multi-line
# English `note`.
WORKSPACE_SOURCE_AMBIGUOUS = 'ambiguous'
WORKSPACE_SOURCE_NO_ACCESS = 'no-access'
WORKSPACE_SOURCE_PERMISSION_DENIED = 'permission-denied'
# The user can read one or more workspaces but write none of them (every
# accessible workspace is read-only-visible to them).
WORKSPACE_SOURCE_READ_ONLY = 'read-only'

# Values for the per-workspace ``read_access`` config key, which controls who
# may read a (private) workspace. 'allowed_users' (default): only the
# workspace's allowed users (its members) can see it — it is hidden from
# everyone else. 'all': anyone can see the workspace and its clusters/jobs, but
# writes stay members-only, so non-members effectively get read-only access.
# Only meaningful for private workspaces; an open (non-private) workspace is
# usable by everyone regardless.
READ_ACCESS_ALLOWED_USERS = 'allowed_users'
READ_ACCESS_ALL = 'all'

# Access levels a request can require on the caller's *active* workspace.
# 'read' means "may I use this workspace as my context" (satisfied by
#  membership OR by the workspace being read-only-visible to non-members);
# 'write' means "may I create resources in this workspace" (membership only).
# Write implies read.
WORKSPACE_ACTION_READ = 'read'
WORKSPACE_ACTION_WRITE = 'write'
