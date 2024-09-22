import os

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_env(monkeypatch, tmp_path):
    """Dummy credentials and config for every test, so nothing can reach a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def ssm():
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


# Obviously fake values. The tests assert these never appear in output.
SECRET_OLD = "placeholder-old-value"
SECRET_NEW = "placeholder-new-value"


@pytest.fixture
def seeded(ssm):
    ssm.put_parameter(Name="/app/test/APP_NAME", Value="orders", Type="String")
    ssm.put_parameter(Name="/app/test/LOG_LEVEL", Value="info", Type="String")
    ssm.put_parameter(Name="/app/test/DB_PASSWORD", Value=SECRET_OLD, Type="SecureString")
    ssm.put_parameter(Name="/app/test/OLD_FLAG", Value="1", Type="String")
    ssm.put_parameter(Name="/app/test/nested/IGNORED", Value="x", Type="String")
    ssm.put_parameter(Name="/app/other/ELSEWHERE", Value="x", Type="String")
    return ssm


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "app.env"
    path.write_text(
        "# settings\n"
        "APP_NAME=orders\n"
        "LOG_LEVEL=debug\n"
        f"DB_PASSWORD='{SECRET_NEW}'\n"
        "NEW_FLAG=on\n"
        "SESSION_SECRET=placeholder-session-value\n"
    )
    return path
