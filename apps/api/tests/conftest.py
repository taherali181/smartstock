"""Test-wide guarantees.

The API test suite must never depend on a network service. The conversation
layer can route to a local model, and that call carries a long read timeout so
a cold model still answers in production. Inside tests that same timeout turns
an unreachable or busy model into what looks like a hang, so the model route is
disabled for the whole suite here rather than per fixture.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _deterministic_environment() -> None:
    """Force the deterministic route and the test environment for every test."""
    os.environ["SMARTSTOCK_ENVIRONMENT"] = "test"
    os.environ["SMARTSTOCK_LLM_ROUTE"] = "deterministic"
    os.environ.setdefault("SMARTSTOCK_AUTH_MODE", "development")

    from smartstock_api.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_outbound_http(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail loudly instead of hanging if a test reaches off the test server.

    TestClient is itself built on httpx, so the guard inspects the destination
    rather than blocking the transport: requests to `testserver` are the suite
    talking to the app, anything else is a real network call. Tests marked
    `external` or `postgres` exercise real services and are exempt.
    """
    if request.node.get_closest_marker("external") or request.node.get_closest_marker("postgres"):
        return

    import httpx

    original = httpx.Client.send

    def guarded(self: httpx.Client, http_request: httpx.Request, *args: object, **kwargs: object):
        if http_request.url.host not in {"testserver", ""}:
            raise AssertionError(
                f"a unit test attempted an outbound HTTP request to "
                f"{http_request.url}; route it through a fake or mark the test `external`"
            )
        return original(self, http_request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded)
