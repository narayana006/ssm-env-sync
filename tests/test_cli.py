import io
import json
import stat

import pytest
from botocore.exceptions import ClientError

from ssm_env_sync import cli, store

from conftest import SECRET_NEW, SECRET_OLD

PATH = "/app/test"


def run(argv, client=None, stdin=None, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    if stdin is not None:
        monkeypatch.setattr("builtins.input", lambda: stdin)
    code = cli.main(argv, client=client, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def values(client):
    return {key: param.value for key, param in store.fetch(client, PATH).items()}


def assert_no_secrets(*texts):
    for text in texts:
        assert SECRET_OLD not in text
        assert SECRET_NEW not in text
        assert "placeholder-session-value" not in text


# --- diff -------------------------------------------------------------------

def test_diff_lists_differences_and_exits_1(seeded, env_file):
    code, out, err = run(["diff", "--path", PATH, "--file", str(env_file)], seeded)
    assert code == 1
    lines = out.splitlines()
    assert lines[0] == f"/app/test in Parameter Store  vs  {env_file}"
    assert "  ~  DB_PASSWORD     SecureString  differs" in lines
    assert "  ~  LOG_LEVEL       String        differs" in lines
    assert "  +  NEW_FLAG        String        only in file" in lines
    assert "  -  OLD_FLAG        String        only in Parameter Store" in lines
    assert "  +  SESSION_SECRET  SecureString  only in file" in lines
    assert not any("APP_NAME" in line for line in lines)
    assert lines[-1] == "1 same, 2 differ, 2 only in file, 1 only in Parameter Store"
    assert "debug" not in out and "info" not in out  # values hidden by default
    assert_no_secrets(out, err)


def test_diff_show_values_reveals_plain_but_masks_secure(seeded, env_file):
    code, out, err = run(["diff", "--path", PATH, "--file", str(env_file), "--show-values", "--all"], seeded)
    assert code == 1
    assert "store='info' file='debug'" in out
    assert "  ~  DB_PASSWORD     SecureString  differs                  ********" in out.splitlines()
    assert "  +  NEW_FLAG        String        only in file             'on'" in out.splitlines()
    assert "=  APP_NAME" in out
    assert_no_secrets(out, err)


def test_diff_exits_0_when_identical(ssm, tmp_path):
    ssm.put_parameter(Name="/app/test/A", Value="1", Type="String")
    env = tmp_path / "a.env"
    env.write_text("A=1\n")
    code, out, _ = run(["diff", "--path", PATH, "--file", str(env)], ssm)
    assert code == 0
    assert out.splitlines()[-1] == "1 same, 0 differ, 0 only in file, 0 only in Parameter Store"


def test_diff_from_file_needs_no_client(tmp_path, env_file):
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"Parameters": [
        {"Name": "/app/test/LOG_LEVEL", "Type": "String", "Value": "debug"},
        {"Name": "/app/test/DB_PASSWORD", "Type": "SecureString", "Value": SECRET_OLD},
    ]}))
    code, out, err = run(["diff", "--path", PATH, "--file", str(env_file), "--from-file", str(export)])
    assert code == 1
    assert "(saved export)" in out
    assert "1 same, 1 differ, 3 only in file, 0 only in Parameter Store" in out
    assert_no_secrets(out, err)


def test_errors_exit_2(seeded, tmp_path, env_file):
    code, _, err = run(["diff", "--path", PATH, "--file", str(tmp_path / "missing.env")], seeded)
    assert (code, err) == (2, f"error: {tmp_path / 'missing.env'}: file not found\n")
    code, _, err = run(["diff", "--path", "no-slash", "--file", str(env_file)], seeded)
    assert code == 2 and "must start with '/'" in err
    bad = tmp_path / "bad.env"
    bad.write_text("TOKEN='placeholder-unterminated\n")
    code, _, err = run(["diff", "--path", PATH, "--file", str(bad)], seeded)
    assert code == 2 and "line 1" in err and "placeholder-unterminated" not in err


# --- push -------------------------------------------------------------------

def test_push_is_dry_run_by_default(seeded, env_file):
    before = values(seeded)
    code, out, err = run(["push", "--path", PATH, "--file", str(env_file), "--prune"], seeded)
    assert code == 0
    assert "Dry run: 5 change(s) not applied." in out
    assert "+  NEW_FLAG        String        create" in out
    assert "-  OLD_FLAG        String        delete" in out
    assert values(seeded) == before
    assert_no_secrets(out, err)


def test_push_apply_yes_writes_and_keeps_types(seeded, env_file):
    code, out, err = run(["push", "--path", PATH, "--file", str(env_file), "--apply", "--yes"], seeded)
    assert code == 0 and "Applied 4 change(s) to /app/test." in out
    params = store.fetch(seeded, PATH)
    assert params["LOG_LEVEL"].value == "debug"
    assert params["DB_PASSWORD"].value == SECRET_NEW and params["DB_PASSWORD"].type == "SecureString"
    assert params["NEW_FLAG"].type == "String"
    assert params["SESSION_SECRET"].type == "SecureString"
    assert "OLD_FLAG" in params  # no --prune, so nothing is deleted
    assert_no_secrets(out, err)
    assert run(["push", "--path", PATH, "--file", str(env_file)], seeded)[1] == \
        f"/app/test is already up to date with {env_file}\n"


def test_push_prune_deletes_store_only_parameters(seeded, env_file):
    code, _, _ = run(["push", "--path", PATH, "--file", str(env_file), "--apply", "--yes", "--prune"], seeded)
    assert code == 0
    assert "OLD_FLAG" not in values(seeded)
    # Parameters outside the path, or nested below it, are never touched.
    assert seeded.get_parameter(Name="/app/test/nested/IGNORED")["Parameter"]["Value"] == "x"
    assert seeded.get_parameter(Name="/app/other/ELSEWHERE")["Parameter"]["Value"] == "x"


@pytest.mark.parametrize("answer, applied", [("y", True), ("yes", True), ("n", False), ("", False)])
def test_push_apply_asks_for_confirmation(seeded, env_file, monkeypatch, answer, applied):
    before = values(seeded)
    code, out, err = run(["push", "--path", PATH, "--file", str(env_file), "--apply"], seeded,
                         stdin=answer, monkeypatch=monkeypatch)
    assert "Apply 4 change(s) to /app/test? [y/N]" in err
    if applied:
        assert code == 0 and values(seeded)["LOG_LEVEL"] == "debug"
    else:
        assert code == 2 and "Aborted" in out and values(seeded) == before


def test_push_apply_without_a_terminal_aborts(seeded, env_file, monkeypatch):
    def no_input():
        raise EOFError
    monkeypatch.setattr("builtins.input", no_input)
    before = values(seeded)
    code, out, _ = run(["push", "--path", PATH, "--file", str(env_file), "--apply"], seeded)
    assert code == 2 and values(seeded) == before


def test_push_refuses_apply_with_from_file(env_file, tmp_path):
    export = tmp_path / "export.json"
    export.write_text('{"Parameters": []}')
    code, _, err = run(["push", "--path", PATH, "--file", str(env_file), "--from-file", str(export), "--apply"])
    assert code == 2 and "--apply cannot be combined with --from-file" in err


def test_push_uses_given_kms_key_for_secure_parameters(seeded, env_file):
    calls = []
    real_put = seeded.put_parameter
    seeded.put_parameter = lambda **kwargs: calls.append(kwargs) or real_put(**kwargs)
    run(["push", "--path", PATH, "--file", str(env_file), "--apply", "--yes", "--kms-key-id", "alias/app-config"],
        seeded)
    by_name = {call["Name"].rsplit("/", 1)[1]: call for call in calls}
    assert by_name["DB_PASSWORD"]["KeyId"] == "alias/app-config"
    assert by_name["SESSION_SECRET"]["KeyId"] == "alias/app-config"
    assert "KeyId" not in by_name["LOG_LEVEL"]
    assert by_name["LOG_LEVEL"]["Overwrite"] is True and by_name["NEW_FLAG"]["Overwrite"] is False


def test_push_update_reuses_existing_kms_key(ssm, tmp_path):
    ssm.put_parameter(Name="/app/test/API_TOKEN", Value=SECRET_OLD, Type="SecureString", KeyId="alias/team-key")
    env = tmp_path / "a.env"
    env.write_text(f"API_TOKEN={SECRET_NEW}\n")
    calls = []
    real_put = ssm.put_parameter
    ssm.put_parameter = lambda **kwargs: calls.append(kwargs) or real_put(**kwargs)
    assert run(["push", "--path", PATH, "--file", str(env), "--apply", "--yes"], ssm)[0] == 0
    assert calls[0]["KeyId"] == "alias/team-key"


def test_push_skips_empty_values(seeded, tmp_path):
    env = tmp_path / "a.env"
    env.write_text("APP_NAME=orders\nLOG_LEVEL=info\nOLD_FLAG=1\nDB_PASSWORD=placeholder-old-value\nBLANK=\n")
    code, out, err = run(["push", "--path", PATH, "--file", str(env), "--apply", "--yes"], seeded)
    assert code == 0
    assert "skipping BLANK" in err and "already up to date" in out


def test_push_failure_stops_and_masks_values(seeded, env_file):
    def failing_put(**kwargs):
        raise ClientError(
            {"Error": {"Code": "ValidationException", "Message": f"bad value {kwargs['Value']}"}}, "PutParameter")
    seeded.put_parameter = failing_put
    code, out, err = run(["push", "--path", PATH, "--file", str(env_file), "--apply", "--yes"], seeded)
    assert code == 2
    assert err.splitlines()[0].startswith("error: /app/test/DB_PASSWORD: ValidationException from PutParameter: ")
    assert "Stopped after 0 of 4 change(s)." in err
    assert_no_secrets(out, err)


# --- pull -------------------------------------------------------------------

def test_pull_is_dry_run_by_default(seeded, env_file):
    original = env_file.read_text()
    code, out, err = run(["pull", "--path", PATH, "--file", str(env_file)], seeded)
    assert code == 0
    assert "Dry run: 3 change(s) not written." in out
    assert "+  OLD_FLAG     String        add to file" in out
    assert env_file.read_text() == original
    assert_no_secrets(out, err)


def test_pull_apply_updates_file_in_place(seeded, env_file):
    code, out, err = run(["pull", "--path", PATH, "--file", str(env_file), "--apply"], seeded)
    assert code == 0
    assert env_file.read_text() == (
        "# settings\n"
        "APP_NAME=orders\n"
        "LOG_LEVEL=info\n"
        f"DB_PASSWORD={SECRET_OLD}\n"
        "NEW_FLAG=on\n"
        "SESSION_SECRET=placeholder-session-value\n"
        "OLD_FLAG=1\n"
    )
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert_no_secrets(out, err)


def test_pull_prune_and_skip_secure(seeded, env_file):
    run(["pull", "--path", PATH, "--file", str(env_file), "--apply", "--prune", "--skip-secure"], seeded)
    assert env_file.read_text() == "# settings\nAPP_NAME=orders\nLOG_LEVEL=info\nOLD_FLAG=1\n"


def test_pull_creates_a_new_private_file(seeded, tmp_path):
    target = tmp_path / "fresh.env"
    code, out, err = run(["pull", "--path", PATH, "--file", str(target), "--apply"], seeded)
    assert code == 0
    assert target.read_text() == f"APP_NAME=orders\nDB_PASSWORD={SECRET_OLD}\nLOG_LEVEL=info\nOLD_FLAG=1\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert_no_secrets(out, err)
    assert run(["pull", "--path", PATH, "--file", str(target)], seeded)[1].startswith(f"{target} is already up to date")


def test_pull_skips_names_that_are_not_valid_env_keys(ssm, tmp_path):
    ssm.put_parameter(Name="/app/test/db.host", Value="h", Type="String")
    ssm.put_parameter(Name="/app/test/GOOD", Value="1", Type="String")
    target = tmp_path / "x.env"
    code, _, err = run(["pull", "--path", PATH, "--file", str(target), "--apply"], ssm)
    assert code == 0 and target.read_text() == "GOOD=1\n"
    assert "skipping /app/test/db.host" in err


def test_safe_error_masks_longest_values_first():
    message = cli.safe_error(ValueError("got abcdef and abc"), ["abc", "abcdef", None, ""])
    assert message == "ValueError: got ******** and ********"


def test_access_denied_is_reported_cleanly(env_file):
    class Denied:
        def get_paginator(self, name):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                              "GetParametersByPath")
    code, _, err = run(["diff", "--path", PATH, "--file", str(env_file)], Denied())
    assert (code, err) == (2, "error: AccessDeniedException from GetParametersByPath: not authorized\n")
