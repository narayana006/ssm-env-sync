"""Parameter Store access: read a path, write and delete parameters.

Every function takes the boto3 SSM client as an argument so tests (and the
offline ``--from-file`` mode) never need real AWS credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

SECURE = "SecureString"


class StoreError(ValueError):
    """Bad path or unreadable export file."""


@dataclass(frozen=True)
class Param:
    name: str
    key: str
    type: str
    # repr=False keeps values out of tracebacks, debuggers and log lines.
    value: str = field(repr=False)

    @property
    def is_secure(self) -> bool:
        return self.type == SECURE


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        raise StoreError("parameter path must start with '/', for example /myapp/prod")
    path = path.rstrip("/")
    if not path:
        raise StoreError("refusing to work on the root path '/'; give a path such as /myapp/prod")
    return path


def _key_for(name: str, path: str) -> str | None:
    """Return the env key for a direct child of ``path``, else None."""
    prefix = path + "/"
    if not name.startswith(prefix):
        return None
    key = name[len(prefix):]
    return key if key and "/" not in key else None


def _collect(raw_params, path: str) -> dict[str, Param]:
    found: dict[str, Param] = {}
    for raw in raw_params:
        key = _key_for(raw["Name"], path)
        if key is not None:
            found[key] = Param(raw["Name"], key, raw.get("Type", "String"), raw.get("Value", ""))
    return found


def fetch(client, path: str) -> dict[str, Param]:
    """Read the direct children of ``path`` (decrypted), keyed by env key."""
    path = normalize_path(path)
    paginator = client.get_paginator("get_parameters_by_path")
    raw = []
    for page in paginator.paginate(Path=path, Recursive=False, WithDecryption=True):
        raw.extend(page.get("Parameters", []))
    return _collect(raw, path)


def load_export(file_path: str, path: str) -> dict[str, Param]:
    """Read parameters from JSON saved from ``get-parameters-by-path``.

    Accepts either the full response object (``{"Parameters": [...]}``) or a
    bare list of parameter objects.
    """
    path = normalize_path(path)
    try:
        with open(file_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        # Only the position is reported; the document may hold secrets.
        raise StoreError(f"{file_path}: not valid JSON (line {exc.lineno})") from None
    if isinstance(data, dict):
        data = data.get("Parameters")
    if not isinstance(data, list) or not all(isinstance(p, dict) and "Name" in p for p in data):
        raise StoreError(f"{file_path}: expected a 'Parameters' list of objects with 'Name'")
    return _collect(data, path)


def existing_key_id(client, name: str) -> str | None:
    """KMS key currently protecting a SecureString parameter, if it can be found."""
    response = client.describe_parameters(
        ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]
    )
    for meta in response.get("Parameters", []):
        if meta.get("Name") == name:
            return meta.get("KeyId")
    return None


def put(client, name: str, value: str, type_: str, *, overwrite: bool, kms_key_id: str | None = None) -> None:
    kwargs = {"Name": name, "Value": value, "Type": type_, "Overwrite": overwrite}
    if type_ == SECURE and kms_key_id:
        kwargs["KeyId"] = kms_key_id
    client.put_parameter(**kwargs)


def delete(client, names: list[str]) -> None:
    for start in range(0, len(names), 10):  # DeleteParameters takes at most 10 names
        client.delete_parameters(Names=names[start:start + 10])
