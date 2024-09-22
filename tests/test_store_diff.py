import json

import pytest

from ssm_env_sync import diff, store


def test_fetch_returns_direct_children_only(seeded):
    params = store.fetch(seeded, "/app/test/")
    assert sorted(params) == ["APP_NAME", "DB_PASSWORD", "LOG_LEVEL", "OLD_FLAG"]
    assert params["DB_PASSWORD"].type == "SecureString"
    assert params["DB_PASSWORD"].value == "placeholder-old-value"  # decrypted


def test_fetch_follows_pagination(ssm):
    for i in range(23):  # GetParametersByPath returns at most 10 per page
        ssm.put_parameter(Name=f"/many/KEY_{i:02d}", Value=str(i), Type="String")
    assert len(store.fetch(ssm, "/many")) == 23


@pytest.mark.parametrize("bad", ["myapp/prod", "/", ""])
def test_path_validation(bad):
    with pytest.raises(store.StoreError):
        store.normalize_path(bad)


def test_param_repr_hides_value():
    param = store.Param("/a/B", "B", "SecureString", "placeholder-hidden")
    assert "placeholder-hidden" not in repr(param)
    entry = diff.Entry("B", diff.CHANGED, "SecureString", local="placeholder-l", remote="placeholder-r")
    assert "placeholder-" not in repr(entry)


def test_load_export_accepts_object_and_list(tmp_path):
    params = [
        {"Name": "/a/b/ONE", "Type": "String", "Value": "1"},
        {"Name": "/a/b/deep/TWO", "Type": "String", "Value": "2"},
        {"Name": "/a/bc/THREE", "Type": "String", "Value": "3"},
    ]
    as_object = tmp_path / "object.json"
    as_object.write_text(json.dumps({"Parameters": params}))
    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps(params))
    assert list(store.load_export(str(as_object), "/a/b")) == ["ONE"]
    assert list(store.load_export(str(as_list), "/a/b")) == ["ONE"]


def test_load_export_rejects_bad_documents(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"Parameters": [{"Name": "/a/X", "Value": "placeholder-leak"')
    with pytest.raises(store.StoreError) as excinfo:
        store.load_export(str(broken), "/a")
    assert "placeholder-leak" not in str(excinfo.value)
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"Something": []}')
    with pytest.raises(store.StoreError):
        store.load_export(str(wrong), "/a")


def test_build_classifies_every_key():
    remote = {
        "SAME": store.Param("/p/SAME", "SAME", "String", "1"),
        "CHANGED": store.Param("/p/CHANGED", "CHANGED", "SecureString", "old"),
        "GONE": store.Param("/p/GONE", "GONE", "StringList", "a,b"),
    }
    local = {"SAME": "1", "CHANGED": "new", "API_TOKEN": "t", "PLAIN_NEW": "v"}
    entries = {e.key: e for e in diff.build(local, remote)}
    assert {k: e.status for k, e in entries.items()} == {
        "SAME": diff.SAME, "CHANGED": diff.CHANGED, "GONE": diff.ONLY_STORE,
        "API_TOKEN": diff.ONLY_FILE, "PLAIN_NEW": diff.ONLY_FILE,
    }
    assert entries["CHANGED"].type == "SecureString"  # existing type is kept
    assert entries["API_TOKEN"].type == "SecureString"  # name matches a default pattern
    assert entries["PLAIN_NEW"].type == "String"
    assert diff.count(list(entries.values())) == {
        diff.SAME: 1, diff.CHANGED: 1, diff.ONLY_FILE: 2, diff.ONLY_STORE: 1,
    }


def test_custom_secure_pattern_is_case_insensitive():
    assert diff.looks_secure("stripe_signing", ["*SIGNING*"])
    assert not diff.looks_secure("LOG_LEVEL", diff.DEFAULT_SECURE_PATTERNS)
