"""Run diff, a dry-run push, a real push and a pull against an in-memory fake.

Nothing here talks to AWS: `moto` replaces the Parameter Store API in this
process, and dummy credentials are set before boto3 is imported.

    pip install -e ".[dev]"
    python examples/moto_demo.py
"""

import json
import os
import pathlib
import shutil
import tempfile

os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_DEFAULT_REGION="us-east-1",
    AWS_SHARED_CREDENTIALS_FILE=os.devnull,  # ignore any real profiles on this machine
    AWS_CONFIG_FILE=os.devnull,
)
os.environ.pop("AWS_PROFILE", None)

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from ssm_env_sync.cli import main  # noqa: E402

HERE = pathlib.Path(__file__).parent


def run(argv, client):
    print("$ ssm-env-sync " + " ".join(argv))
    code = main(argv, client=client)
    print(f"[exit {code}]\n")


with mock_aws(), tempfile.TemporaryDirectory() as workdir:
    os.chdir(workdir)
    shutil.copy(HERE / "prod.env.example", "prod.env")
    ssm = boto3.client("ssm")
    for param in json.loads((HERE / "parameters.json").read_text())["Parameters"]:
        ssm.put_parameter(Name=param["Name"], Value=param["Value"], Type=param["Type"])

    target = ["--path", "/myapp/prod", "--file", "prod.env"]
    run(["diff", *target], ssm)
    run(["push", *target, "--show-values"], ssm)
    run(["push", *target, "--apply", "--yes"], ssm)
    run(["diff", *target], ssm)
    run(["pull", *target, "--apply"], ssm)
    run(["diff", *target, "--all"], ssm)
