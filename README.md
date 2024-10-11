# ssm-env-sync

Compare a `.env` file with an AWS Systems Manager Parameter Store path, then pull or push the differences. Every write is a dry run until you add `--apply`.

**Status:** `diff`, `pull` and `push` work today, including SecureString parameters and an offline mode that reads a saved JSON export instead of calling AWS. It has been tested against [moto](https://github.com/getmoto/moto) (an in-memory fake of the AWS API) only; see [Limitations](#limitations).

## Install

```bash
pip install git+https://github.com/narayana006/ssm-env-sync
```

Python 3.10 or newer. The only runtime dependency is `boto3`.

## Try it without an AWS account

The repository ships an example `.env` file and an example export of a parameter path. All values in them are placeholders.

```bash
git clone https://github.com/narayana006/ssm-env-sync && cd ssm-env-sync
ssm-env-sync diff --path /myapp/prod --file examples/prod.env --from-file examples/parameters.json
```

Output of that command (run against the example files, no AWS involved):

```
/myapp/prod in examples/parameters.json (saved export)  vs  examples/prod.env
  ~  DB_PASSWORD           SecureString  differs
  +  FEATURE_NEW_CHECKOUT  String        only in file
  -  LEGACY_QUEUE_NAME     String        only in Parameter Store
  ~  LOG_LEVEL             String        differs
  +  PAYMENT_API_TOKEN     SecureString  only in file
2 same, 2 differ, 2 only in file, 1 only in Parameter Store
```

`diff` exits with 1 when anything differs, 0 when the two sides match and 2 on errors, so it can gate a CI job.

Add `--show-values --all` to see non-secret values and the keys that match. SecureString values stay masked:

```
/myapp/prod in examples/parameters.json (saved export)  vs  examples/prod.env
  =  APP_NAME              String        same                     'orders-api'
  ~  DB_PASSWORD           SecureString  differs                  ********
  +  FEATURE_NEW_CHECKOUT  String        only in file             'true'
  -  LEGACY_QUEUE_NAME     String        only in Parameter Store  'orders-v1'
  ~  LOG_LEVEL             String        differs                  store='info' file='debug'
  =  MAX_WORKERS           String        same                     '8'
  +  PAYMENT_API_TOKEN     SecureString  only in file             ********
2 same, 2 differ, 2 only in file, 1 only in Parameter Store
```

## A full round trip

`examples/moto_demo.py` loads the example parameters into moto and runs the real CLI code against it. This is its output (from `python examples/moto_demo.py`, against moto, not against AWS):

```
$ ssm-env-sync diff --path /myapp/prod --file prod.env
/myapp/prod in Parameter Store  vs  prod.env
  ~  DB_PASSWORD           SecureString  differs
  +  FEATURE_NEW_CHECKOUT  String        only in file
  -  LEGACY_QUEUE_NAME     String        only in Parameter Store
  ~  LOG_LEVEL             String        differs
  +  PAYMENT_API_TOKEN     SecureString  only in file
2 same, 2 differ, 2 only in file, 1 only in Parameter Store
[exit 1]

$ ssm-env-sync push --path /myapp/prod --file prod.env --show-values
push prod.env -> /myapp/prod
  ~  DB_PASSWORD           SecureString  update  ********
  +  FEATURE_NEW_CHECKOUT  String        create  'true'
  ~  LOG_LEVEL             String        update  store='info' file='debug'
  +  PAYMENT_API_TOKEN     SecureString  create  ********
Dry run: 4 change(s) not applied. Re-run with --apply to write to Parameter Store.
[exit 0]

$ ssm-env-sync push --path /myapp/prod --file prod.env --apply --yes
push prod.env -> /myapp/prod
  ~  DB_PASSWORD           SecureString  update
  +  FEATURE_NEW_CHECKOUT  String        create
  ~  LOG_LEVEL             String        update
  +  PAYMENT_API_TOKEN     SecureString  create
Applied 4 change(s) to /myapp/prod.
[exit 0]

$ ssm-env-sync diff --path /myapp/prod --file prod.env
/myapp/prod in Parameter Store  vs  prod.env
  -  LEGACY_QUEUE_NAME  String  only in Parameter Store
6 same, 0 differ, 0 only in file, 1 only in Parameter Store
[exit 1]

$ ssm-env-sync pull --path /myapp/prod --file prod.env --apply
pull /myapp/prod -> prod.env
  +  LEGACY_QUEUE_NAME  String  add to file
Wrote 1 change(s) to prod.env (file mode 600).
[exit 0]

$ ssm-env-sync diff --path /myapp/prod --file prod.env --all
/myapp/prod in Parameter Store  vs  prod.env
  =  APP_NAME              String        same
  =  DB_PASSWORD           SecureString  same
  =  FEATURE_NEW_CHECKOUT  String        same
  =  LEGACY_QUEUE_NAME     String        same
  =  LOG_LEVEL             String        same
  =  MAX_WORKERS           String        same
  =  PAYMENT_API_TOKEN     SecureString  same
7 same, 0 differ, 0 only in file, 0 only in Parameter Store
[exit 0]
```

## Commands

A parameter named `/myapp/prod/LOG_LEVEL` maps to the key `LOG_LEVEL` when `--path` is `/myapp/prod`. Only direct children of the path are considered.

```bash
ssm-env-sync diff --path /myapp/prod --file .env.prod            # what differs?
ssm-env-sync pull --path /myapp/prod --file .env.prod            # dry run: Parameter Store -> file
ssm-env-sync pull --path /myapp/prod --file .env.prod --apply    # write the file
ssm-env-sync push --path /myapp/prod --file .env.prod            # dry run: file -> Parameter Store
ssm-env-sync push --path /myapp/prod --file .env.prod --apply    # ask for confirmation, then write
```

Options shared by all three commands:

| Option | Meaning |
| --- | --- |
| `--path PATH` | Parameter Store path. Must start with `/`; the root path `/` is refused. |
| `--file FILE` | The `.env` file. |
| `--from-file JSON` | Read parameters from a saved `get-parameters-by-path` response instead of calling AWS. |
| `--profile`, `--region` | Passed to `boto3.Session`. Without them the normal AWS credential chain is used. |
| `--show-values` | Show String and StringList values. SecureString values are shown as `********`. |
| `--secure GLOB` | Extra name pattern for keys that should be created as SecureString (repeatable). |

### pull

Updates changed keys in place and appends new ones, keeping comments, blank lines, key order and `export` prefixes. The file is written with mode `600` because it may now contain decrypted secrets.

- `--apply` writes the file. Without it nothing is written.
- `--prune` also removes keys that do not exist in Parameter Store.
- `--skip-secure` leaves SecureString parameters out of the file.

Parameter names that are not valid `.env` keys (for example `db.host`) are skipped with a warning.

### push

- `--apply` is required to change anything. It then asks `Apply N change(s) to PATH? [y/N]`; `--yes` skips the question. If there is no terminal to answer on, the push is aborted.
- `--prune` also deletes parameters that are not in the file. Without it, push never deletes.
- `--kms-key-id` sets the KMS key (ID, ARN or alias) for SecureString parameters.

How the parameter type is chosen:

- A parameter that already exists keeps its type. When a SecureString is updated and `--kms-key-id` is not given, the tool looks up the key the parameter currently uses and passes the same key again.
- A new key is created as SecureString when its name matches one of the built-in patterns (`*SECRET*`, `*PASSWORD*`, `*PASSWD*`, `*TOKEN*`, `*CREDENTIAL*`, `*PRIVATE*`, `*_KEY`, `*APIKEY*`, `*_DSN`, `*DATABASE_URL*`, case-insensitive) or a `--secure` pattern. Otherwise it is created as String. The dry run shows the type each key will get, so check it before applying.
- New SecureString parameters use `--kms-key-id` if given, otherwise the account's default Parameter Store key.

Keys with an empty value are skipped with a warning, because Parameter Store does not accept empty values. If a write fails, push stops, reports how many changes were applied and exits with 2.

### Saving an export for `--from-file`

`--from-file` accepts the JSON printed by:

```bash
aws ssm get-parameters-by-path --path /myapp/prod --with-decryption > params.json
```

Without `--with-decryption` the SecureString values in the file are ciphertext and will always show as different. With it, `params.json` contains secrets in plain text: treat it like the `.env` file and do not commit it. `push --apply` cannot be combined with `--from-file`.

## How secrets are handled

- SecureString values are never written to the terminal, with any combination of flags. The same applies to new keys that will be created as SecureString.
- All other values are hidden too unless you pass `--show-values`.
- Error messages name the parameter and the AWS error code. Any parameter value that appears in an AWS error message is replaced with `********`. `.env` and JSON parse errors report the line number only.
- The tool has no log file and no debug logging.
- `pull --apply` necessarily writes decrypted values into the `.env` file. That is the one place secret values go.

A secret stored as a plain String parameter is not known to be a secret, so `--show-values` will display it.

## IAM permissions

| Command | Actions |
| --- | --- |
| `diff`, `pull`, and the dry run of `push` | `ssm:GetParametersByPath` |
| `push --apply` | `ssm:GetParametersByPath`, `ssm:PutParameter`, and `ssm:DescribeParameters` (used to look up the current KMS key when updating a SecureString without `--kms-key-id`) |
| `push --apply --prune` | the above plus `ssm:DeleteParameters` |

For SecureString parameters encrypted with a customer managed KMS key you also need `kms:Decrypt` on that key to read and `kms:Encrypt` to write. With `--from-file` no AWS call is made and no permissions are needed.

## `.env` syntax

`KEY=value` lines, an optional `export ` prefix, `#` comment lines, and blank lines. Unquoted values end at a ` #` comment. Single-quoted values are literal. Double-quoted values understand `\n`, `\r`, `\t`, `\"` and `\\`. There is no variable interpolation, and a value cannot span several lines (use `\n` in double quotes). If a key appears twice the last one wins.

## Features

- [x] Diff a `.env` file against a Parameter Store path
- [x] Pull parameters into a `.env` file
- [x] Push with a dry run and confirmation
- [x] SecureString support with your KMS key
- [x] Never prints secret values (SecureString is always masked; other values need `--show-values`)
- [x] Offline mode from a saved JSON export
- [ ] Recursive paths (nested parameters mapped to keys)
- [ ] Machine-readable (JSON) diff output
- [ ] Tags, descriptions and advanced-tier parameters on push

## Limitations

- Tested against moto and `botocore` stubs only. It has not been run against a real AWS account yet, so KMS behaviour in particular (key lookup on update, permissions) is verified only as far as moto models it.
- Direct children of the path only. `/myapp/prod/db/HOST` is ignored when the path is `/myapp/prod`.
- StringList parameters are compared and written as their raw comma-separated string.
- Push does not set tags, descriptions, tiers or policies. New parameters get the account defaults; updates leave existing metadata alone.
- Push is not atomic. Parameters are written one at a time, and a failure part-way leaves the earlier writes in place.
- Parameter Store limits (value size, name characters, API rate) are enforced by AWS, not checked up front. Throttled calls are retried with botocore's standard retry mode.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The tests set dummy credentials and run entirely against moto.

## Contributing

Issues and pull requests are welcome. Please include a test for any behaviour change, and never put real parameter values in tests, examples or issue reports.

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
