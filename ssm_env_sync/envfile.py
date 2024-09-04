"""Read and write .env files.

Supported syntax: ``KEY=value``, an optional ``export`` prefix, ``#`` comments,
blank lines, single-quoted values (literal) and double-quoted values with
``\\n``, ``\\r``, ``\\t``, ``\\"`` and ``\\\\`` escapes. Values are never
interpolated. A value cannot span several physical lines.

Error messages mention the line number only, never the line content, because
the content may be a secret.
"""

from __future__ import annotations

import re

_ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
_UNESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class EnvFileError(ValueError):
    """The .env file could not be parsed."""


def is_valid_key(key: str) -> bool:
    return _KEY.fullmatch(key) is not None


def _parse_value(raw: str, lineno: int) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        out = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                out.append(_ESCAPES.get(nxt, "\\" + nxt))
                i += 2
                continue
            if ch == '"':
                _check_trailing(raw[i + 1:], lineno)
                return "".join(out)
            out.append(ch)
            i += 1
        raise EnvFileError(f"line {lineno}: unterminated double-quoted value")
    if raw.startswith("'"):
        end = raw.find("'", 1)
        if end == -1:
            raise EnvFileError(f"line {lineno}: unterminated single-quoted value")
        _check_trailing(raw[end + 1:], lineno)
        return raw[1:end]
    # Unquoted: a '#' preceded by whitespace starts a comment.
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _check_trailing(rest: str, lineno: int) -> None:
    rest = rest.strip()
    if rest and not rest.startswith("#"):
        raise EnvFileError(f"line {lineno}: unexpected text after closing quote")


def parse(text: str) -> dict[str, str]:
    """Parse .env text into an ordered ``{key: value}`` dict (last duplicate wins)."""
    values: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            raise EnvFileError(f"line {lineno}: expected KEY=value")
        values[match.group(2)] = _parse_value(match.group(3), lineno)
    return values


def format_value(value: str) -> str:
    """Quote ``value`` so that :func:`parse` reads it back unchanged."""
    if value == "" or _PLAIN_VALUE.fullmatch(value):
        return value
    if "'" not in value and not any(c in value for c in "\n\r\t"):
        return f"'{value}'"
    return '"' + "".join(_UNESCAPES.get(c, c) for c in value) + '"'


def format_line(key: str, value: str, prefix: str = "") -> str:
    return f"{prefix}{key}={format_value(value)}"


def update_text(text: str, updates: dict[str, str], removals: set[str] | None = None) -> str:
    """Apply changes to .env text while keeping comments, order and blank lines.

    Existing keys in ``updates`` are rewritten in place; new keys are appended
    in sorted order; keys in ``removals`` are dropped.
    """
    removals = removals or set()
    pending = dict(updates)
    out: list[str] = []
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        stripped = line.strip()
        if not match or not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        key = match.group(2)
        if key in removals:
            continue
        if key in updates:
            # A duplicate key later in the file is rewritten too, so the
            # last-one-wins rule cannot resurrect the old value.
            out.append(format_line(key, updates[key], match.group(1)))
            pending.pop(key, None)
        else:
            out.append(line)
    for key in sorted(pending):
        out.append(format_line(key, pending[key]))
    return "\n".join(out) + "\n" if out else ""
