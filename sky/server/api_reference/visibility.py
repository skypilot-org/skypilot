"""Which API server routes the public API reference documents.

Every route is matched against the rules in order: ``RULES`` first, then the
rules that plugins register. The first rule whose pattern matches the whole
path decides. An internal or unlisted pattern also matches the paths under it.
A route that no rule matches is public, so a route that is not meant for API
clients must be covered by an internal or unlisted rule.
"""

import dataclasses
import enum
import re
from typing import Tuple

from sky.server.api_reference import extensions


class Visibility(enum.Enum):
    PUBLIC = 'public'
    # A real API that the reference does not cover yet.
    UNLISTED = 'unlisted'
    # Serves the dashboard, a browser sign-in flow, or operators.
    INTERNAL = 'internal'


@dataclasses.dataclass(frozen=True)
class Rule:
    patterns: Tuple[str, ...]
    visibility: Visibility
    reason: str

    def matches(self, path: str) -> bool:
        # An internal or unlisted pattern also covers the routes under it.
        under = '' if self.visibility is Visibility.PUBLIC else '(?:/.*)?'
        return any(re.fullmatch(f'(?:{p}){under}', path) for p in self.patterns)


RULES: Tuple[Rule, ...] = (
    Rule(
        (r'/', r'/dashboard/.*', r'/dashboard_config', r'/api/plugins'),
        Visibility.INTERNAL,
        'dashboard page, asset or UI helper',
    ),
    Rule(
        (r'/token', r'/auth/authorize', r'/api/v1/auth/(authorize|token)'),
        Visibility.INTERNAL,
        'browser or CLI sign-in flow',
    ),
    Rule((r'/api/completion/.*',), Visibility.INTERNAL, 'CLI shell completion'),
    Rule((r'/debug/.*',), Visibility.INTERNAL, 'operator debugging tool'),
    Rule((r'/local_(up|down)',), Visibility.INTERNAL,
         'local development cluster'),
)

DEFAULT = Rule((r'.*',), Visibility.PUBLIC, 'SkyPilot API')


def classify(path: str) -> Rule:
    """The rule for a route."""
    for rule in RULES + extensions.rules():
        if rule.matches(path):
            return rule
    return DEFAULT
