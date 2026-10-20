import os


PROJECT_ID = "transport-for-london-511318"


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


def get_tfl_api_key():
    """Use the environment variable or retrieve the cloud secret."""
    api_key = os.getenv("TFL_API_KEY")
    if api_key:
        return api_key

    return get_secret("tfl-api-key")
