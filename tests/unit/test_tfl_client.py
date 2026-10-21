"""Unit tests for src/clients/tfl.py. No network: the opener is faked."""

import email.message
import io
import json
import urllib.error
import urllib.parse

import pytest

from src.clients.tfl import (
    TflClient,
    TflHttpError,
    TflRequestError,
    TflResponseError,
)

API_KEY = "test-key-should-never-leak"


class FakeResponse:
    def __init__(self, body=b"[]", status=200, headers=None, read_error=None):
        self._body = body
        self.status = status
        self.headers = email.message.Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self._read_error = read_error

    def read(self):
        if self._read_error:
            raise self._read_error
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, headers=None):
    message = email.message.Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(
        f"https://api.tfl.gov.uk/x?app_key={API_KEY}",
        code, "error", message, io.BytesIO(b""),
    )


class FakeOpener:
    """Returns or raises the queued outcomes in order, recording requests."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_client(opener, **kwargs):
    sleeps = []
    client = TflClient(
        API_KEY,
        opener=opener,
        sleep=sleeps.append,
        rng=lambda: 0.5,
        **kwargs,
    )
    return client, sleeps


# --- Successful requests -----------------------------------------------------

def test_success_returns_parsed_json_and_metadata():
    body = json.dumps([{"id": "victoria"}]).encode()
    opener = FakeOpener(FakeResponse(body, headers={"Cache-Control": "public, max-age=30"}))
    client, sleeps = make_client(opener)

    response = client.get_json("/Line/Mode/tube/Status", {"detail": "false"})

    assert response.data == [{"id": "victoria"}]
    assert response.status == 200
    assert response.attempts == 1
    assert response.size_bytes == len(body)
    assert response.cache_max_age == 30
    assert response.params == {"detail": "false"}
    assert sleeps == []


def test_request_has_key_params_and_json_headers():
    opener = FakeOpener(FakeResponse())
    client, _ = make_client(opener, timeout=12)

    client.get_json("/Line/Mode/tube/Status", {"detail": "false"})

    request, timeout = opener.requests[0]
    url = urllib.parse.urlparse(request.full_url)
    query = urllib.parse.parse_qs(url.query)
    assert url.path == "/Line/Mode/tube/Status"
    assert query == {"detail": ["false"], "app_key": [API_KEY]}
    assert request.get_header("Accept") == "application/json"
    assert timeout == 12


# --- Retries and backoff -----------------------------------------------------

def test_retries_temporary_server_error_then_succeeds():
    opener = FakeOpener(http_error(503), http_error(502), FakeResponse(b"[1]"))
    client, sleeps = make_client(opener)

    response = client.get_json("/Line/Mode/tube/Status")

    assert response.data == [1]
    assert response.attempts == 3
    assert len(sleeps) == 2


def test_backoff_grows_exponentially_with_jitter():
    opener = FakeOpener(http_error(500), http_error(500), http_error(500), FakeResponse())
    client, sleeps = make_client(opener, backoff_base=1.0)

    client.get_json("/x")

    # rng fixed at 0.5: delay/2 + 0.5 * delay/2 = 0.75 * delay; delays 1, 2, 4.
    assert sleeps == [0.75, 1.5, 3.0]


def test_backoff_is_capped():
    client, _ = make_client(FakeOpener(), backoff_base=1.0, backoff_max=5.0)
    assert client._backoff_delay(10, None) <= 5.0


def test_rate_limit_obeys_retry_after_header():
    opener = FakeOpener(http_error(429, {"Retry-After": "7"}), FakeResponse())
    client, sleeps = make_client(opener)

    client.get_json("/x")

    assert sleeps == [7.0]


def test_connection_reset_during_read_is_retried():
    opener = FakeOpener(
        FakeResponse(read_error=ConnectionResetError(54, "Connection reset by peer")),
        FakeResponse(b"[]"),
    )
    client, sleeps = make_client(opener)

    response = client.get_json("/Line/Meta/Severity")

    assert response.attempts == 2
    assert len(sleeps) == 1


def test_timeout_is_retried():
    opener = FakeOpener(TimeoutError(), FakeResponse())
    client, _ = make_client(opener)
    assert client.get_json("/x").attempts == 2


# --- Giving up -----------------------------------------------------------------

@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(code):
    opener = FakeOpener(http_error(code))
    client, sleeps = make_client(opener)

    with pytest.raises(TflHttpError) as info:
        client.get_json("/x")

    assert info.value.status == code
    assert info.value.attempts == 1
    assert sleeps == []


def test_gives_up_after_max_attempts_on_http_error():
    opener = FakeOpener(*[http_error(503)] * 3)
    client, sleeps = make_client(opener, max_attempts=3)

    with pytest.raises(TflHttpError) as info:
        client.get_json("/x")

    assert info.value.attempts == 3
    assert len(sleeps) == 2


def test_gives_up_after_max_attempts_on_network_error():
    opener = FakeOpener(*[ConnectionResetError()] * 2)
    client, _ = make_client(opener, max_attempts=2)

    with pytest.raises(TflRequestError) as info:
        client.get_json("/x")

    assert info.value.error_type == "ConnectionResetError"
    assert info.value.attempts == 2


def test_invalid_json_raises_response_error():
    client, _ = make_client(FakeOpener(FakeResponse(b"<html>oops</html>")))
    with pytest.raises(TflResponseError):
        client.get_json("/x")


# --- Secret safety ---------------------------------------------------------------

@pytest.mark.parametrize("outcomes", [
    [http_error(404)],
    [http_error(503)] * 4,
    [ConnectionResetError()] * 4,
    [FakeResponse(b"not json")],
])
def test_errors_never_contain_the_api_key(outcomes):
    client, _ = make_client(FakeOpener(*outcomes))

    with pytest.raises(Exception) as info:
        client.get_json("/x")

    assert API_KEY not in str(info.value)
    assert API_KEY not in repr(info.value)
    assert info.value.__cause__ is None  # original error (with URL) not chained


def test_client_repr_hides_key():
    client, _ = make_client(FakeOpener())
    assert API_KEY not in repr(client)


def test_retry_log_does_not_contain_key(caplog):
    opener = FakeOpener(http_error(503), FakeResponse())
    client, _ = make_client(opener)

    with caplog.at_level("WARNING", logger="tfl.client"):
        client.get_json("/x")

    assert "retrying" in caplog.text
    assert API_KEY not in caplog.text


def test_successful_response_does_not_store_key():
    client, _ = make_client(FakeOpener(FakeResponse()))
    response = client.get_json("/x", {"detail": "false"})
    assert API_KEY not in repr(response)


# --- Input validation ----------------------------------------------------------

def test_rejects_empty_key():
    with pytest.raises(ValueError):
        TflClient("")


def test_rejects_key_in_params():
    client, _ = make_client(FakeOpener())
    with pytest.raises(ValueError):
        client.get_json("/x", {"app_key": "other"})


def test_rejects_path_without_leading_slash():
    client, _ = make_client(FakeOpener())
    with pytest.raises(ValueError):
        client.get_json("Line/Mode/tube/Status")
