"""Pylint plugin that flags calls to functions this repo bans.

Loaded through `load-plugins` in `.pylintrc`. Each entry maps the qualified
name astroid infers for the callee to the reason and the replacement, so the
check follows the function through `import x as y` and `from x import f`.
Silence a deliberate use with `# pylint: disable=banned-api` and a comment
saying why.
"""
from typing import Dict

from astroid import nodes
from pylint import checkers
from pylint import interfaces
from pylint import lint

_BANNED: Dict[str, str] = {
    # python/cpython#86296: before Python 3.12, a cancel of the calling task
    # that lands as the inner future completes is swallowed, and the caller
    # keeps running.
    'asyncio.tasks.wait_for':
        ('it can lose a cancel of the calling task on Python < 3.12; use '
         'sky.utils.asyncio_utils.wait_for'),
}


class BannedApiChecker(checkers.BaseChecker):
    """Flags calls whose target is in `_BANNED`."""

    __implements__ = interfaces.IAstroidChecker

    name = 'banned-api'
    msgs = {
        'W9901': ('%s is banned: %s', 'banned-api',
                  'Used when code calls a function this repository bans.'),
    }

    def visit_call(self, node: nodes.Call) -> None:
        try:
            inferred = list(node.func.infer())
        except Exception:  # pylint: disable=broad-except
            # Inference failures are routine (dynamic attributes, missing
            # stubs); they are not this checker's business.
            return
        for target in inferred:
            qname = getattr(target, 'qname', None)
            if qname is None:
                continue
            reason = _BANNED.get(qname())
            if reason is not None:
                self.add_message('banned-api',
                                 node=node,
                                 args=(node.func.as_string(), reason))
                return


def register(linter: lint.PyLinter) -> None:
    linter.register_checker(BannedApiChecker(linter))
