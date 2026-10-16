"""Helpers for handling Python source text.

Useful when shipping a script inline through a transport that has a
size budget — e.g. base64'd into a Pod spec or a cloud user-data
field. Pair with ``gzip`` + ``base64`` at the call site.
"""
import io
import token
import tokenize
from typing import List


def minify_python_source(source: str) -> str:
    """Strip comments and module/class/function docstrings.

    Stdlib-only. The result must still parse and execute identically;
    only comment tokens and docstring-position string literals are
    dropped. Identifiers, non-docstring string literals, and control
    flow are untouched.
    """
    out: List[str] = []
    prev_toktype = token.INDENT
    last_lineno = -1
    last_col = 0
    readline = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(readline):
        toktype, tokstr, (sline, scol), (eline, ecol), _ = tok
        if sline > last_lineno:
            last_col = 0
        if scol > last_col and toktype != tokenize.COMMENT:
            out.append(' ' * (scol - last_col))
        if toktype == tokenize.COMMENT:
            pass
        elif toktype == token.STRING and prev_toktype in (token.INDENT,
                                                          token.NEWLINE):
            # String that is its own statement at module/class/function
            # top — that's a docstring. Drop it.
            pass
        else:
            out.append(tokstr)
        prev_toktype = toktype
        last_col = ecol
        last_lineno = eline
    return ''.join(out)
