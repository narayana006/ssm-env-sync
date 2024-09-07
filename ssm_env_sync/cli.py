"""Command-line interface: ``ssm-env-sync diff | pull | push``.

Output rules, enforced here:

* SecureString values are never written to stdout or stderr, with any flag.
* Other values are shown only with ``--show-values``.
* ``pull`` and ``push`` change nothing unless ``--apply`` is given.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, diff, envfile, store
from .diff import CHANGED, ONLY_FILE, ONLY_STORE, SAME

EXIT_OK, EXIT_DIFFERENT, EXIT_ERROR = 0, 1, 2

_LABELS = {
    CHANGED: ("~", "differs"),
    ONLY_FILE: ("+", "only in file"),
    ONLY_STORE: ("-", "only in Parameter Store"),
    SAME: ("=", "same"),
}


class UsageError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", required=True, help="Parameter Store path, e.g. /myapp/prod (direct children only)")
    common.add_argument("--file", required=True, help=".env file to compare with")
    common.add_argument("--from-file", metavar="JSON",
                        help="read parameters from saved get-parameters-by-path JSON instead of calling AWS")
    common.add_argument("--profile", help="AWS profile name (default: the standard credential chain)")
    common.add_argument("--region", help="AWS region (default: from your AWS configuration)")
    common.add_argument("--show-values", action="store_true",
                        help="show String/StringList values; SecureString values stay masked")
    common.add_argument("--secure", action="append", default=[], metavar="GLOB",
                        help="also treat new keys matching this glob as SecureString (repeatable)")

    parser = argparse.ArgumentParser(
        prog="ssm-env-sync",
        description="Diff, pull and push between a .env file and a Parameter Store path.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_diff = sub.add_parser("diff", parents=[common], help="show what differs (exit 1 if anything does)")
    p_diff.add_argument("--all", action="store_true", help="also list keys that are the same")

    p_pull = sub.add_parser("pull", parents=[common], help="write Parameter Store values into the .env file")
    p_pull.add_argument("--apply", action="store_true", help="actually write the file (default: dry-run)")
    p_pull.add_argument("--prune", action="store_true", help="remove keys from the file that are not in Parameter Store")
    p_pull.add_argument("--skip-secure", action="store_true", help="leave SecureString parameters out of the file")

    p_push = sub.add_parser("push", parents=[common], help="write .env values into Parameter Store")
    p_push.add_argument("--apply", action="store_true", help="actually write to Parameter Store (default: dry-run)")
    p_push.add_argument("--yes", action="store_true", help="with --apply, skip the confirmation prompt")
    p_push.add_argument("--prune", action="store_true", help="delete parameters that are not in the file")
    p_push.add_argument("--kms-key-id", help="KMS key ID, ARN or alias for SecureString parameters")
    return parser


def make_client(args):
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    return session.client("ssm", config=Config(retries={"mode": "standard", "max_attempts": 8}))


def read_env(path: str, *, missing_ok: bool = False) -> tuple[str, dict[str, str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        if missing_ok:
            return "", {}
        raise UsageError(f"{path}: file not found") from None
    try:
        return text, envfile.parse(text)
    except envfile.EnvFileError as exc:
        raise UsageError(f"{path}: {exc}") from None


def load_remote(args, client):
    if args.from_file:
        return store.load_export(args.from_file, args.path), None
    client = client or make_client(args)
    return store.fetch(client, args.path), client


def describe(entry, show_values: bool) -> str:
    """Value column for a row: empty by default, masked for SecureString."""
    if not show_values:
        return ""
    if entry.is_secure:
        return f"  {diff.MASK}"
    if entry.status == CHANGED:
        return f"  store={entry.remote!r} file={entry.local!r}"
    return f"  {(entry.local if entry.remote is None else entry.remote)!r}"


def print_table(rows: list[tuple[str, ...]], out) -> None:
    if not rows:
        return
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        print(("  " + "  ".join(cells) + row[-1]).rstrip(), file=out)


def summary(entries) -> str:
    totals = diff.count(entries)
    return (f"{totals[SAME]} same, {totals[CHANGED]} differ, {totals[ONLY_FILE]} only in file, "
            f"{totals[ONLY_STORE]} only in Parameter Store")


def cmd_diff(args, client, out, err) -> int:
    _, local = read_env(args.file)
    remote, _ = load_remote(args, client)
    entries = diff.build(local, remote, _patterns(args))
    source = f"{args.from_file} (saved export)" if args.from_file else "Parameter Store"
    print(f"{store.normalize_path(args.path)} in {source}  vs  {args.file}", file=out)
    rows = []
    for entry in entries:
        if entry.status == SAME and not args.all:
            continue
        symbol, label = _LABELS[entry.status]
        rows.append((symbol, entry.key, entry.type, label, describe(entry, args.show_values)))
    print_table(rows, out)
    print(summary(entries), file=out)
    different = any(entry.status != SAME for entry in entries)
    return EXIT_DIFFERENT if different else EXIT_OK


def cmd_pull(args, client, out, err) -> int:
    text, local = read_env(args.file, missing_ok=True)
    remote, _ = load_remote(args, client)
    usable = {}
    for key, param in remote.items():
        if not envfile.is_valid_key(key):
            print(f"warning: skipping {param.name}: '{key}' is not a valid .env key", file=err)
        elif args.skip_secure and param.is_secure:
            continue
        else:
            usable[key] = param
    entries = diff.build(local, usable, _patterns(args))
    updates, removals, rows = {}, set(), []
    for entry in entries:
        if entry.status == CHANGED:
            updates[entry.key] = entry.remote
            rows.append(("~", entry.key, entry.type, "update in file", describe(entry, args.show_values)))
        elif entry.status == ONLY_STORE:
            updates[entry.key] = entry.remote
            rows.append(("+", entry.key, entry.type, "add to file", describe(entry, args.show_values)))
        elif entry.status == ONLY_FILE and args.prune:
            removals.add(entry.key)
            rows.append(("-", entry.key, "", "remove from file", ""))
    if not rows:
        print(f"{args.file} is already up to date with {store.normalize_path(args.path)}", file=out)
        return EXIT_OK
    print(f"pull {store.normalize_path(args.path)} -> {args.file}", file=out)
    print_table(rows, out)
    if not args.apply:
        print(f"Dry run: {len(rows)} change(s) not written. Re-run with --apply to write {args.file}.", file=out)
        return EXIT_OK
    write_private(args.file, envfile.update_text(text, updates, removals))
    print(f"Wrote {len(rows)} change(s) to {args.file} (file mode 600).", file=out)
    return EXIT_OK


def write_private(path: str, text: str) -> None:
    """Write the file readable by the owner only; it may now hold secrets."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def cmd_push(args, client, out, err) -> int:
    if args.apply and args.from_file:
        raise UsageError("--apply cannot be combined with --from-file: a saved export cannot be written to")
    _, local = read_env(args.file)
    remote, client = load_remote(args, client)
    entries = diff.build(local, remote, _patterns(args))
    path = store.normalize_path(args.path)
    actions, rows = [], []
    for entry in entries:
        if entry.status in (CHANGED, ONLY_FILE):
            if entry.local == "":
                print(f"warning: skipping {entry.key}: Parameter Store does not accept empty values", file=err)
                continue
            verb = "update" if entry.status == CHANGED else "create"
            actions.append((verb, entry))
            rows.append(("~" if verb == "update" else "+", entry.key, entry.type, verb, describe(entry, args.show_values)))
        elif entry.status == ONLY_STORE and args.prune:
            actions.append(("delete", entry))
            rows.append(("-", entry.key, entry.type, "delete", ""))
    if not actions:
        print(f"{path} is already up to date with {args.file}", file=out)
        return EXIT_OK
    print(f"push {args.file} -> {path}", file=out)
    print_table(rows, out)
    if not args.apply:
        print(f"Dry run: {len(actions)} change(s) not applied. Re-run with --apply to write to Parameter Store.",
              file=out)
        return EXIT_OK
    if not args.yes and not confirm(f"Apply {len(actions)} change(s) to {path}? [y/N] ", err):
        print("Aborted; nothing was changed.", file=out)
        return EXIT_ERROR
    return apply_push(client, path, actions, args.kms_key_id, out, err)


def confirm(prompt: str, err) -> bool:
    print(prompt, end="", file=err, flush=True)
    try:
        answer = input()
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def apply_push(client, path, actions, kms_key_id, out, err) -> int:
    done = 0
    current = path
    try:
        for verb, entry in actions:
            if verb == "delete":
                continue
            current = f"{path}/{entry.key}"
            key_id = kms_key_id
            if verb == "update" and entry.is_secure and not key_id:
                # Re-use the key the parameter is already encrypted with.
                key_id = store.existing_key_id(client, current)
            store.put(client, current, entry.local, entry.type, overwrite=(verb == "update"), kms_key_id=key_id)
            done += 1
        to_delete = [f"{path}/{entry.key}" for verb, entry in actions if verb == "delete"]
        current = f"{len(to_delete)} parameter(s) to delete"
        store.delete(client, to_delete)
        done += len(to_delete)
    except Exception as exc:  # botocore errors; reported without any values
        secrets = [e.local for _, e in actions] + [e.remote for _, e in actions]
        print(f"error: {current}: {safe_error(exc, secrets)}", file=err)
        print(f"Stopped after {done} of {len(actions)} change(s).", file=err)
        return EXIT_ERROR
    print(f"Applied {done} change(s) to {path}.", file=out)
    return EXIT_OK


def safe_error(exc: Exception, values=()) -> str:
    """Describe an exception without echoing any parameter value it may quote.

    For AWS service errors the error code and operation are taken from the
    structured response; only the free-text message is filtered.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict) and "Error" in response:
        prefix = f"{response['Error'].get('Code', 'Unknown')} from {getattr(exc, 'operation_name', 'AWS')}"
        message = response["Error"].get("Message", "")
    else:
        prefix, message = type(exc).__name__, str(exc)
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        message = message.replace(value, diff.MASK)
    return f"{prefix}: {message}" if message else prefix


def _patterns(args):
    return tuple(diff.DEFAULT_SECURE_PATTERNS) + tuple(args.secure)


def main(argv=None, client=None, out=None, err=None) -> int:
    """Entry point. ``client`` lets tests pass a mocked or stubbed SSM client."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    handler = {"diff": cmd_diff, "pull": cmd_pull, "push": cmd_push}[args.command]
    try:
        return handler(args, client, out, err)
    except (UsageError, store.StoreError) as exc:
        print(f"error: {exc}", file=err)
        return EXIT_ERROR
    except Exception as exc:
        if type(exc).__module__.startswith(("botocore", "boto3")):
            print(f"error: {safe_error(exc)}", file=err)
            return EXIT_ERROR
        raise


if __name__ == "__main__":
    sys.exit(main())
