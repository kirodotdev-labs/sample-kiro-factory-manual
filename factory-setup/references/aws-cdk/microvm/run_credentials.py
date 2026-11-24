"""AWS credentials for one worker run, from the token vending machine (lib/functions/token-vending.ts).

The MicroVM's execution role may only invoke the token vending machine. worker.py calls setup() with
the run token from the run hook payload, which points every AWS SDK in the run at a config whose
credential_process is this script: it invokes the function as the execution role, read from instance
metadata, and prints the session it returns, a session of the runtime role scoped to the run's
worker. The session lasts an hour, and SDKs run this again before it ends. gh-wrapper.sh runs it with
`github` for the run's GitHub token.
"""

import json
import os
import sys
from pathlib import Path

TOKEN_FILE = Path("/tmp/factory/secrets/run-token")
CONFIG_FILE = Path("/tmp/factory/secrets/aws-config")


def setup(token: str) -> None:
    if not token:
        raise ValueError("the run hook payload carries no run token")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.touch(mode=0o600)
    TOKEN_FILE.write_text(token)
    CONFIG_FILE.write_text(
        f"[default]\nregion = {os.environ['FACTORY_REGION']}\n"
        f"credential_process = {sys.executable} {Path(__file__).resolve()}\n"
    )
    # Credentials in the environment would win over credential_process.
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        os.environ.pop(name, None)
    os.environ["AWS_CONFIG_FILE"] = str(CONFIG_FILE)


def token() -> str:
    return TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""


def _invoke(request: dict) -> dict:
    import boto3
    from botocore.credentials import InstanceMetadataFetcher, InstanceMetadataProvider

    fetcher = InstanceMetadataFetcher(timeout=2, num_attempts=3)
    role = InstanceMetadataProvider(iam_role_fetcher=fetcher).load()
    if role is None:
        raise RuntimeError("no execution role credentials in instance metadata")
    frozen = role.get_frozen_credentials()
    client = boto3.client(
        "lambda",
        region_name=os.environ["FACTORY_REGION"],
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
    )
    response = client.invoke(
        FunctionName=os.environ["FACTORY_TOKEN_VENDING_FUNCTION"], Payload=json.dumps(request).encode()
    )
    body = json.loads(response["Payload"].read() or b"{}")
    if response.get("FunctionError"):
        raise RuntimeError(f"token vending machine: {body.get('errorMessage', response['FunctionError'])}")
    return body


def terminate(microvm_id: str) -> None:
    _invoke({"token": token(), "terminate": microvm_id})


if __name__ == "__main__":
    # `github` prints a GitHub App installation token for the image's gh wrapper; no argument prints the
    # AWS session for credential_process.
    if sys.argv[1:] == ["github"]:
        print(_invoke({"token": token(), "github": True})["token"])
    else:
        print(json.dumps(_invoke({"token": token()})))
