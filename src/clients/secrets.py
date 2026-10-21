import os
from pathlib import Path


PROJECT_ID = "transport-for-london-511318"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def get_secret(secret_id):
    """Retrieve a secret from Google Secret Manager."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = (
        f"projects/{PROJECT_ID}/secrets/"
        f"{secret_id}/versions/latest"
    )
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def read_env_file(name, env_file=ENV_FILE):
    """Return one value from the project .env file, or None."""
    if not env_file.is_file():
        return None

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'") or None

    return None


def get_tfl_api_key():
    """Find the TfL API key.

    Order: environment variable, then the local .env file (development),
    then Google Secret Manager (cloud).
    """
    api_key = os.getenv("TFL_API_KEY") or read_env_file("TFL_API_KEY")
    if api_key:
        return api_key

    return get_secret("tfl-api-key")
