"""Compose file subset check: reject constructs the built-in parser lacks.

The built-in compose parser in this shim cannot read YAML anchors,
aliases, merge keys, or block scalars, cannot read more than one
document per stream, and cannot follow the top-level ``include`` key
or the service-level ``extends`` and ``profiles`` keys.
:func:`check_subset` scans a compose file text line by line and raises
:class:`SubsetError` naming the offending line, so the user is told to
install the real docker-compose.
"""
from __future__ import annotations

import re


class SubsetError(Exception):
    """A compose construct the built-in parser does not support.

    ``construct`` is the rejected construct (``"anchor"``,
    ``"alias"``, ``"merge key"``, ``"block scalar"``,
    ``"multi-document stream"``, ``"include"``, ``"extends"`` or
    ``"profiles"``) and ``line`` is its 1-based line number in the
    original file text.
    """

    def __init__(self, construct: str, line: int) -> None:
        super().__init__(
            "line %d: %s is not supported by the built-in compose parser; "
            "install docker-compose (brew install docker-compose)"
            % (line, construct)
        )
        self.construct = construct
        self.line = line


# An anchor or alias must start the value token: '&' or '*' followed by
# at least one name character.
_ANCHOR = re.compile(r"^&[A-Za-z0-9_-]+")
_ALIAS = re.compile(r"^\*[A-Za-z0-9_-]+")

# A block scalar is '|' or '>' alone, or followed by one chomping
# indicator ('+' or '-') and/or one explicit indentation indicator
# (digit), in either order: '|', '|-', '|+2', '|2+' and so on.
_BLOCK_SCALAR = re.compile(r"^[|>](?:[+-]?[0-9]?$|[0-9]?[+-]?$)")

# A YAML document marker, recognized only in column 0: '---' starts a
# document and '...' ends one, each followed by whitespace or end of
# line (an indented '---' or '...' would be a plain scalar, not a
# marker).
_DOC_START = re.compile(r"^---(?:\s|$)")
_DOC_END = re.compile(r"^\.{3}(?:\s|$)")

# An anchor or alias inside a flow collection: '&' or '*' with a name,
# directly after the collection opener, an item separator, or a
# mapping value marker, with optional spaces in between.
_FLOW_ANCHOR = re.compile(r"[\[{,]\s*&[A-Za-z0-9_-]+|:\s*&[A-Za-z0-9_-]+")
_FLOW_ALIAS = re.compile(r"[\[{,]\s*\*[A-Za-z0-9_-]+|:\s*\*[A-Za-z0-9_-]+")

# Quoted strings are data and are removed before flow scanning, so
# '*' and '&' inside them cannot match.
_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')


def _map_key(line: str) -> str:
    """The map key of a line: the text before the first ``:``.

    A leading ``- `` sequence marker (and spaces) are stripped first, so
    ``- <<: *base`` reports the key ``<<``. Lines without a colon return
    their stripped content; a quoted key would never equal ``<<``.
    """
    content = line.lstrip()
    if content.startswith("- "):
        content = content[2:].lstrip()
    colon = content.find(":")
    if colon == -1:
        return content
    return content[:colon].strip()


def _key_indent(line: str) -> int:
    """The 0-based column of the map key of a line.

    Leading spaces and each ``- `` sequence marker are counted, so the
    key of ``  - key: value`` sits at column 4 and a column-0
    ``key: value`` at column 0.
    """
    indent = len(line) - len(line.lstrip())
    content = line.lstrip()
    while content.startswith("- "):
        content = content[2:].lstrip()
        indent += 2
    return indent


def _flow_reference(token: str) -> str | None:
    """``"anchor"``, ``"alias"`` or ``None`` for a flow collection token.

    Quoted strings (``"..."`` and ``'...'``) are removed first, so data
    such as ``["*a", "a && b"]`` cannot match. What remains is searched
    for an anchor or alias right after ``[``/``{``/``,`` or ``: ``.
    """
    bare = _QUOTED.sub("", token)
    if _FLOW_ANCHOR.search(bare):
        return "anchor"
    if _FLOW_ALIAS.search(bare):
        return "alias"
    return None


def _value_token(line: str) -> str:
    """The raw value token of a compose line, without leading spaces.

    For ``key: value`` the token is the text after the first ``": "``
    (nothing when the line ends with ``":"`` and the value is a nested
    block). For a ``- item`` sequence line the token is the text after
    ``"- "``; when the item is itself ``key: value`` only the value
    counts, so a bare ``- *alias`` still points at the alias.
    """
    content = line.lstrip()
    if content.startswith("- "):
        content = content[2:]
    if content.endswith(":"):
        return ""
    separator = content.find(": ")
    if separator != -1:
        content = content[separator + 2:]
    return content.lstrip()


def check_subset(text: str) -> None:
    """Raise :class:`SubsetError` for a construct the parser cannot read.

    Blank and comment-only lines are skipped. A single leading document
    start marker (a column-0 ``---``) is allowed, but any later ``---``
    and any ``...`` document end marker are rejected as
    ``"multi-document stream"``. The map key ``include`` at column 0 is
    rejected, as are ``extends`` and ``profiles`` keys at any deeper
    indentation. A quoted value token (starting with ``"`` or ``'``) is
    data and never matches, so ``"&x"``, ``a && b`` inside a quoted
    string, URLs with ``&`` or globs cannot trigger a false positive;
    inside a flow collection (a token starting with ``[`` or ``{``)
    quoted strings are removed before anchor/alias detection, so
    ``["*a", b]`` is data. A value token that is a block scalar header
    -- a bare ``|`` or ``>`` optionally followed by a chomping or
    indentation indicator (``|-``, ``>+``, ``|2``) -- is rejected as
    ``"block scalar"``; a pipe inside a longer plain or quoted scalar
    (``a | b``) is data and is allowed. Returns ``None`` when the text
    stays within the supported subset.
    """
    seen_marker = False
    seen_content = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and _DOC_END.match(stripped):
            raise SubsetError("multi-document stream", lineno)
        if indent == 0 and _DOC_START.match(stripped):
            if seen_marker or seen_content:
                raise SubsetError("multi-document stream", lineno)
            seen_marker = True
            remainder = stripped[3:].strip()
            if not remainder or remainder.startswith("#"):
                continue
            # Content on the marker line is still content: check it below.
            line = remainder
        seen_content = True
        key = _map_key(line)
        key_indent = _key_indent(line)
        if key == "include" and key_indent == 0:
            raise SubsetError("include", lineno)
        if key == "extends" and key_indent > 0:
            raise SubsetError("extends", lineno)
        if key == "profiles" and key_indent > 0:
            raise SubsetError("profiles", lineno)
        if key == "<<":
            raise SubsetError("merge key", lineno)
        token = _value_token(line)
        if not token or token[0] in "'\"":
            continue
        if _ANCHOR.match(token):
            raise SubsetError("anchor", lineno)
        if _ALIAS.match(token):
            raise SubsetError("alias", lineno)
        if token[0] in "[{":
            reference = _flow_reference(token)
            if reference is not None:
                raise SubsetError(reference, lineno)
        # A trailing YAML comment (' #...') is not part of the token;
        # strip it so that 'command: | # note' still matches.
        bare = re.split(r" +#", token, maxsplit=1)[0].rstrip()
        if _BLOCK_SCALAR.match(bare):
            raise SubsetError("block scalar", lineno)
    return None
