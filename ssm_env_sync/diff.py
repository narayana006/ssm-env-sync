"""Compare a parsed .env file with a set of Parameter Store parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from .store import SECURE, Param

SAME = "same"
CHANGED = "changed"
ONLY_FILE = "only_file"
ONLY_STORE = "only_store"

MASK = "********"

# Keys that do not exist in Parameter Store yet are created as SecureString
# when their name matches one of these patterns (case-insensitive).
DEFAULT_SECURE_PATTERNS = (
    "*SECRET*", "*PASSWORD*", "*PASSWD*", "*TOKEN*", "*CREDENTIAL*",
    "*PRIVATE*", "*_KEY", "*APIKEY*", "*_DSN", "*DATABASE_URL*",
)


@dataclass(frozen=True)
class Entry:
    key: str
    status: str
    type: str
    local: str | None = field(default=None, repr=False)
    remote: str | None = field(default=None, repr=False)

    @property
    def is_secure(self) -> bool:
        return self.type == SECURE


def looks_secure(key: str, patterns) -> bool:
    upper = key.upper()
    return any(fnmatchcase(upper, pattern.upper()) for pattern in patterns)


def build(local: dict[str, str], remote: dict[str, Param], secure_patterns=DEFAULT_SECURE_PATTERNS) -> list[Entry]:
    """Return one entry per key, sorted by key.

    An existing parameter keeps its type. A key that exists only in the file
    gets the type it would be created with.
    """
    entries = []
    for key in sorted(set(local) | set(remote)):
        param = remote.get(key)
        if param is None:
            type_ = SECURE if looks_secure(key, secure_patterns) else "String"
            entries.append(Entry(key, ONLY_FILE, type_, local=local[key]))
        elif key not in local:
            entries.append(Entry(key, ONLY_STORE, param.type, remote=param.value))
        else:
            status = SAME if local[key] == param.value else CHANGED
            entries.append(Entry(key, status, param.type, local=local[key], remote=param.value))
    return entries


def count(entries: list[Entry]) -> dict[str, int]:
    totals = {SAME: 0, CHANGED: 0, ONLY_FILE: 0, ONLY_STORE: 0}
    for entry in entries:
        totals[entry.status] += 1
    return totals


def display(value: str | None, entry: Entry, show_values: bool) -> str:
    """Render a value for the terminal. SecureString values are always masked."""
    if entry.is_secure or not show_values or value is None:
        return MASK
    return repr(value)
