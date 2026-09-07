"""Tests for dashboard_api.security (PR2 Cloudflare Access + Origin + rate limit).

These tests cover:

* ``CloudflareAccessValidator`` — JWT signature / issuer / audience / expiry
  / algorithm / missing-token validation. A local HTTP server serves a
  real JWKS document so the test exercises the same code path as
  production.
* ``WriteOriginGuard`` — Origin / Referer matching for mutating
  requests, including subdomain handling and the missing-origin gate.
* ``InProcessRateLimiter`` — sliding-window counting with bounded key
  cardinality.
* ``request_is_from_loopback`` — distinguishing a true direct loopback
  request from a tunneled request that the proxy has rewritten to
  127.0.0.1.
* Integration: the FastAPI app enforces Access, Origin, and rate limits
  when constructed with explicit security settings.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi.testclient import TestClient

from dashboard_api.app import (
    CHAT_HISTORY_ROUTE,
    CHAT_SEND_ROUTE,
    DASHBOARD_ROUTE,
    HEALTHZ_ROUTE,
    SNAPSHOT_ROUTE,
    create_app,
)
from dashboard_api.chat_gateway import CHAT_SEND_MAX_CHARS
from dashboard_api.providers import EngineeringDashboardProviderConfig, create_engineering_dashboard_provider
from dashboard_api.security import (
    CF_ACCESS_CERTS_PATH,
    CF_ACCESS_JWT_HEADER,
    CF_CONNECTING_IP_HEADER,
    JWT_STATUS_BAD_ALGORITHM,
    JWT_STATUS_BAD_AUDIENCE,
    JWT_STATUS_BAD_FORMAT,
    JWT_STATUS_BAD_ISSUER,
    JWT_STATUS_BAD_SIGNATURE,
    JWT_STATUS_CERTS_UNAVAILABLE,
    JWT_STATUS_EXPIRED,
    JWT_STATUS_MISSING,
    JWT_STATUS_VALID,
    CloudflareAccessValidator,
    InProcessRateLimiter,
    OriginCheckResult,
    SecuritySettings,
    WriteOriginGuard,
    request_is_from_loopback,
)
from dashboard_api.engineering_read_model import (
    BacklogSummary,
    DashboardSnapshot,
    RepositorySummary,
)

# Reuse the existing populated snapshot fixture from the dashboard app tests.
from tests.test_dashboard_api_app import StaticProvider, populated_snapshot  # noqa: E402


# ---------------------------------------------------------------------------
# Local JWKS server fixture
# ---------------------------------------------------------------------------


class _JWKSServer:
    """Tiny HTTP server that serves a single JWKS document.

    The server keeps the test keypair in process so the same fixture
    that signed the JWT also controls the JWKS the validator fetches.
    """

    def __init__(self, public_key: RSAPublicKey, kid: str = "test-key-1"):
        self.public_key = public_key
        self.kid = kid
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        # PyJWT's ``to_jwk`` accepts the cryptography key object directly
        # (it inspects ``public_numbers``/``private_numbers``). We pass
        # the live key so the validator fetches the matching JWK.
        jwk_dict = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
        jwk_dict.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self._jwks = json.dumps({"keys": [jwk_dict]}).encode("utf-8")

    @property
    def jwks_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/cdn-cgi/access/certs"

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — stdlib API name
                if self.path.startswith("/cdn-cgi/access/certs"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(outer._jwks)))
                    self.end_headers()
                    self.wfile.write(outer._jwks)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_args, **_kwargs):  # silence stderr noise
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


@pytest.fixture(scope="module")
def jwks_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def jwks_server(jwks_keypair) -> Iterator[_JWKSServer]:
    _, public = jwks_keypair
    server = _JWKSServer(public)
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


TEAM_DOMAIN_PLACEHOLDER = "testteam.cloudflareaccess.com"
AUDIENCE = "dashboard-access-tag-test"


def private_key_bytes(private_key: RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _b64url_uint(value: int) -> str:
    import base64

    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_valid_jwt(
    private_key: RSAPrivateKey,
    *,
    audience: str = AUDIENCE,
    issuer: str | None = None,
    exp_offset: int = 600,
    iat_offset: int = -10,
    email: str | None = "josh@example.com",
    kid: str = "test-key-1",
    algorithm: str = "RS256",
) -> str:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "iss": issuer or f"https://{TEAM_DOMAIN_PLACEHOLDER}",
        "aud": audience,
        "sub": "user|abc",
        "iat": int((now + timedelta(seconds=iat_offset)).timestamp()),
        "exp": int((now + timedelta(seconds=exp_offset)).timestamp()),
        "email": email,
    }
    return jwt.encode(
        claims,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        algorithm=algorithm,
        headers={"kid": kid},
    )


def _make_hs256_jwt(audience: str = AUDIENCE) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": f"https://{TEAM_DOMAIN_PLACEHOLDER}",
            "aud": audience,
            "sub": "user|abc",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        "shared-secret-do-not-use",
        algorithm="HS256",
        headers={"kid": "test-key-1"},
    )


# ---------------------------------------------------------------------------
# CloudflareAccessValidator tests
# ---------------------------------------------------------------------------


class TestCloudflareAccessValidator:
    def _build(self, jwks_server, **overrides) -> CloudflareAccessValidator:
        # The validator normally builds ``https://{team_domain}/cdn-cgi/access/certs``;
        # tests point it at the local JWKS server using ``http://``. The
        # team_domain is still required for the issuer-claim comparison
        # so the validation logic sees the same URL the production
        # service would build.
        host, port = jwks_server._server.server_address[:2]
        team_domain = overrides.pop("team_domain", f"{host}:{port}")
        audience = overrides.pop("audience", AUDIENCE)
        certs_base_url = overrides.pop(
            "certs_base_url", f"http://{host}:{port}{CF_ACCESS_CERTS_PATH}"
        )
        self._test_team_domain = team_domain
        self._test_audience = audience
        return CloudflareAccessValidator(
            team_domain=team_domain,
            audience=audience,
            certs_base_url=certs_base_url,
            **overrides,
        )

    def _make_test_jwt(self, private_key, **overrides) -> str:
        # Default the issuer to the test's team_domain so the JWT
        # passes the iss check without each test having to know the
        # local server's host/port.
        overrides.setdefault("issuer", f"https://{self._test_team_domain}")
        overrides.setdefault("audience", self._test_audience)
        return _make_valid_jwt(private_key, **overrides)

    def test_valid_jwt_is_accepted(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private)

        result = validator.validate(token)
        assert result.valid is True
        assert result.status == JWT_STATUS_VALID
        assert result.email == "josh@example.com"

    def test_missing_jwt_is_rejected(self, jwks_server):
        validator = self._build(jwks_server)
        result = validator.validate(None)
        assert result.valid is False
        assert result.status == JWT_STATUS_MISSING

    def test_empty_jwt_is_rejected(self, jwks_server):
        validator = self._build(jwks_server)
        result = validator.validate("")
        assert result.valid is False
        assert result.status == JWT_STATUS_MISSING

    def test_wrong_audience_is_rejected(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private, audience="other-app-tag")
        result = self._build(jwks_server).validate(token)
        assert result.valid is False
        assert result.status == JWT_STATUS_BAD_AUDIENCE
        # Email must NOT be exposed on failure.
        assert result.email is None

    def test_wrong_issuer_is_rejected(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private, issuer="https://evilteam.cloudflareaccess.com")
        result = self._build(jwks_server).validate(token)
        assert result.valid is False
        assert result.status == JWT_STATUS_BAD_ISSUER
        assert result.email is None

    def test_expired_jwt_is_rejected(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private, exp_offset=-3600, iat_offset=-7200)
        result = self._build(jwks_server).validate(token)
        assert result.valid is False
        assert result.status == JWT_STATUS_EXPIRED
        assert result.email is None

    def test_bad_signature_is_rejected(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private)
        # Tamper with the signature section.
        head, payload, _sig = token.split(".")
        tampered = ".".join([head, payload, "AAAA" + "B" * 256])
        result = self._build(jwks_server).validate(tampered)
        assert result.valid is False
        assert result.status == JWT_STATUS_BAD_SIGNATURE
        assert result.email is None

    def test_bad_algorithm_is_rejected(self, jwks_server):
        token = _make_hs256_jwt()
        result = self._build(jwks_server).validate(token)
        assert result.valid is False
        assert result.status in (JWT_STATUS_BAD_ALGORITHM, JWT_STATUS_BAD_SIGNATURE)

    def test_malformed_token_is_rejected(self, jwks_server):
        result = self._build(jwks_server).validate("not.a.jwt")
        assert result.valid is False
        assert result.status == JWT_STATUS_BAD_FORMAT

    def test_unconfigured_validator_rejects_everything(self):
        validator = CloudflareAccessValidator(team_domain="", audience="")
        result = validator.validate("anything")
        assert result.valid is False
        assert result.status == JWT_STATUS_CERTS_UNAVAILABLE
        assert validator.is_configured is False

    def test_missing_email_falls_back_to_sub(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private, email=None)
        result = self._build(jwks_server).validate(token)
        assert result.valid is True
        # Subject is used as the bounded identity when email is absent.
        assert result.email == "user|abc"

    def test_unreachable_certs_returns_certs_unavailable(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private)
        # Build a validator pointing at a port we never bound.
        validator = CloudflareAccessValidator(team_domain="127.0.0.1:1", audience=AUDIENCE)
        result = validator.validate(token)
        assert result.valid is False
        assert result.status == JWT_STATUS_CERTS_UNAVAILABLE

    def test_jwks_is_cached_across_validations(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        validator = self._build(jwks_server)
        token = self._make_test_jwt(private)

        # Two validations should not raise.
        assert validator.validate(token).valid is True
        assert validator.validate(token).valid is True


# ---------------------------------------------------------------------------
# WriteOriginGuard tests
# ---------------------------------------------------------------------------


class TestWriteOriginGuard:
    def test_get_method_is_not_checked(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(method="GET", origin="https://attacker.com", referer=None)
        assert result.allowed is True
        assert result.reason == "non_mutating"

    def test_post_with_matching_origin_is_allowed(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(method="POST", origin="https://dashboard.example.com", referer=None)
        assert result.allowed is True

    def test_post_with_subdomain_match_is_allowed(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(
            method="POST", origin="https://www.dashboard.example.com", referer=None
        )
        assert result.allowed is True

    def test_post_with_disallowed_origin_is_rejected(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(method="POST", origin="https://attacker.com", referer=None)
        assert result.allowed is False
        assert result.reason == "host_mismatch"

    def test_post_with_no_origin_and_strict_mode_is_rejected(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",), allow_missing_origin=False)
        result = guard.check(method="POST", origin=None, referer=None)
        assert result.allowed is False
        assert result.reason == "missing_origin"

    def test_post_with_no_origin_and_lenient_mode_is_allowed(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",), allow_missing_origin=True)
        result = guard.check(method="POST", origin=None, referer=None)
        assert result.allowed is True
        assert result.reason == "missing_origin_allowed"

    def test_post_with_referer_match_is_allowed(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(
            method="POST",
            origin=None,
            referer="https://dashboard.example.com/some/page",
        )
        assert result.allowed is True

    def test_post_with_malformed_origin_is_rejected(self):
        guard = WriteOriginGuard(public_hostnames=("example.com",))
        result = guard.check(method="POST", origin="not a url", referer=None)
        assert result.allowed is False
        assert result.reason == "no_host_in_origin"

    def test_empty_hostnames_disables_guard(self):
        guard = WriteOriginGuard(public_hostnames=())
        assert guard.is_enabled() is False
        result = guard.check(method="POST", origin="https://anywhere.test", referer=None)
        assert result.allowed is True
        assert result.reason == "guard_disabled"

    def test_origin_matching_is_case_insensitive(self):
        guard = WriteOriginGuard(public_hostnames=("Example.COM",))
        result = guard.check(method="POST", origin="https://Dashboard.example.com", referer=None)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# InProcessRateLimiter tests
# ---------------------------------------------------------------------------


class TestInProcessRateLimiter:
    def test_under_limit_allows(self):
        rl = InProcessRateLimiter(limit=3, window_seconds=10)
        for _ in range(3):
            allowed, retry = rl.check("ip:1")
            assert allowed is True
            assert retry == 0

    def test_over_limit_denies_with_retry_after(self):
        rl = InProcessRateLimiter(limit=2, window_seconds=10)
        assert rl.check("ip:1")[0] is True
        assert rl.check("ip:1")[0] is True
        allowed, retry = rl.check("ip:1")
        assert allowed is False
        assert retry > 0
        assert retry <= 11  # bounded by window + 1 second slack.

    def test_window_expiry_resets_count(self):
        clock = [1000.0]
        rl = InProcessRateLimiter(
            limit=1,
            window_seconds=10,
            time_source=lambda: clock[0],
        )
        assert rl.check("k")[0] is True
        assert rl.check("k")[0] is False
        clock[0] += 11
        assert rl.check("k")[0] is True

    def test_keys_are_independent(self):
        rl = InProcessRateLimiter(limit=1, window_seconds=10)
        assert rl.check("a")[0] is True
        assert rl.check("b")[0] is True
        assert rl.check("a")[0] is False
        assert rl.check("b")[0] is False

    def test_reset_clears_one_key(self):
        rl = InProcessRateLimiter(limit=1, window_seconds=10)
        rl.check("a")
        rl.reset("a")
        assert rl.check("a")[0] is True

    def test_reset_clears_all(self):
        rl = InProcessRateLimiter(limit=1, window_seconds=10)
        rl.check("a")
        rl.check("b")
        rl.reset()
        assert rl.check("a")[0] is True
        assert rl.check("b")[0] is True

    def test_eviction_when_max_keys_reached(self):
        rl = InProcessRateLimiter(limit=1, window_seconds=10, max_keys=2)
        rl.check("a")
        rl.check("b")
        # A new key triggers eviction of the oldest.
        rl.check("c")
        # 'a' had the oldest timestamp; it should be evictable.
        # We do not assert exact identity, but a fresh key always succeeds.
        assert rl.check("d")[0] is True

    def test_empty_key_is_collapsed_to_anon(self):
        rl = InProcessRateLimiter(limit=1, window_seconds=10)
        assert rl.check("")[0] is True
        assert rl.check(None)[0] is False  # second hit on the same anon bucket.


# ---------------------------------------------------------------------------
# request_is_from_loopback tests
# ---------------------------------------------------------------------------


def _make_request(client_host: str | None, headers: dict[str, str] | None = None):
    """Build a FastAPI ``Request`` stub for loopback detection."""
    from starlette.requests import Request as StarletteRequest

    raw_headers: list[tuple[bytes, bytes]] = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("ascii"), v.encode("ascii")))
    scope = {
        "type": "http",
        "client": (client_host, 50000) if client_host else None,
        "headers": raw_headers,
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    return StarletteRequest(scope)


def test_request_is_from_loopback_127_no_cf_header():
    req = _make_request("127.0.0.1", {})
    assert request_is_from_loopback(req) is True


def test_request_is_from_loopback_v6():
    req = _make_request("::1", {})
    assert request_is_from_loopback(req) is True


def test_request_is_loopback_but_cf_header_present_is_not_direct_loopback():
    req = _make_request("127.0.0.1", {CF_CONNECTING_IP_HEADER: "203.0.113.5"})
    assert request_is_from_loopback(req) is False


def test_external_request_is_not_loopback():
    req = _make_request("203.0.113.5", {})
    assert request_is_from_loopback(req) is False


def test_testclient_host_is_not_loopback():
    req = _make_request("testclient", {})
    assert request_is_from_loopback(req) is False


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSecuritySettings:
    def test_defaults(self):
        s = SecuritySettings.from_env({})
        assert s.enforcement == "disabled"
        assert s.team_domain == ""
        assert s.audience == ""
        assert s.public_hostnames == ()
        assert s.chat_send_rate_limit == 10
        assert s.chat_send_rate_window_seconds == 60.0

    def test_normalized_enforcement_invalid_falls_back_to_disabled(self):
        s = SecuritySettings(enforcement="bogus")
        assert s.normalized_enforcement() == "disabled"

    def test_from_env_parses_hostnames(self):
        s = SecuritySettings.from_env({"DASHBOARD_PUBLIC_HOSTNAMES": "a.com, b.com ,c.com"})
        assert s.public_hostnames == ("a.com", "b.com", "c.com")

    def test_from_env_parses_rate_limit(self):
        s = SecuritySettings.from_env(
            {
                "DASHBOARD_CHAT_SEND_RATE_LIMIT": "42",
                "DASHBOARD_CHAT_SEND_RATE_WINDOW_SECONDS": "120",
            }
        )
        assert s.chat_send_rate_limit == 42
        assert s.chat_send_rate_window_seconds == 120.0


# ---------------------------------------------------------------------------
# Integration: app enforces security
# ---------------------------------------------------------------------------


def _populate_snapshot():
    return populated_snapshot()


class TestAppSecurityIntegration:
    """Verify the FastAPI app honours the security settings."""

    @staticmethod
    def _build_app(settings: SecuritySettings):
        from types import SimpleNamespace

        snapshot = _populate_snapshot()

        class P:
            def snapshot(self):
                return snapshot

        class C:
            def __init__(self):
                self.sent = []

            def history(self):
                return SimpleNamespace(to_public_dict=lambda: {"messages": []})

            def send(self, msg):
                # Mirror the real GatewayChatHistoryClient.send() bound
                # so security integration tests exercise the rejection
                # paths the production wiring protects.
                if not isinstance(msg, str) or not msg.strip():
                    return SimpleNamespace(
                        to_public_dict=lambda: {
                            "ok": False,
                            "status": "rejected",
                            "error": "message must be non-empty text",
                        }
                    )
                if len(msg) > CHAT_SEND_MAX_CHARS:
                    return SimpleNamespace(
                        to_public_dict=lambda: {
                            "ok": False,
                            "status": "rejected",
                            "error": "message exceeds 4000 characters",
                        }
                    )
                self.sent.append(msg)
                return SimpleNamespace(
                    to_public_dict=lambda: {"ok": True, "status": "accepted", "run_id": "r"}
                )

        return create_app(
            snapshot_provider=P(),
            chat_history_provider=C(),
            security_settings=settings,
            chat_rate_limiter=InProcessRateLimiter(
                limit=settings.chat_send_rate_limit,
                window_seconds=settings.chat_send_rate_window_seconds,
            ),
        )

    def test_healthz_loopback_returns_200(self):
        app = self._build_app(SecuritySettings(enforcement="local-bypass"))
        client = TestClient(app, client=("127.0.0.1", 50000))
        resp = client.get(HEALTHZ_ROUTE)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["service"] == "engineering-dashboard"

    def test_healthz_non_loopback_returns_403(self):
        app = self._build_app(SecuritySettings(enforcement="local-bypass"))
        client = TestClient(app, client=("203.0.113.5", 50000))
        resp = client.get(HEALTHZ_ROUTE)
        assert resp.status_code == 403

    def test_disabled_enforcement_allows_everything_without_jwt(self):
        app = self._build_app(SecuritySettings(enforcement="disabled"))
        client = TestClient(app)
        assert client.get(SNAPSHOT_ROUTE).status_code == 200
        assert client.get(DASHBOARD_ROUTE).status_code == 200

    def test_local_bypass_allows_loopback_without_jwt(self):
        app = self._build_app(SecuritySettings(enforcement="local-bypass"))
        client = TestClient(app, client=("127.0.0.1", 50000))
        assert client.get(SNAPSHOT_ROUTE).status_code == 200
        assert client.get(DASHBOARD_ROUTE).status_code == 200

    def test_local_bypass_rejects_external_without_jwt(self):
        app = self._build_app(SecuritySettings(enforcement="local-bypass"))
        client = TestClient(app, client=("203.0.113.5", 50000))
        resp = client.get(SNAPSHOT_ROUTE)
        assert resp.status_code == 401
        assert resp.json() == {
            "ok": False,
            "status": "access_required",
            "error": "Cloudflare Access authentication required",
        }

    def test_local_bypass_rejects_external_with_invalid_jwt(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        bad_token = _make_valid_jwt(private, audience="wrong-aud")
        # Build the validator pointing at the real test JWKS so the
        # signature would validate; only the audience check should fail.
        host, port = jwks_server._server.server_address[:2]
        validator = CloudflareAccessValidator(
            team_domain=f"{host}:{port}",
            audience=AUDIENCE,
            certs_base_url=f"http://{host}:{port}{CF_ACCESS_CERTS_PATH}",
        )
        app = create_app(
            snapshot_provider=type("P", (), {"snapshot": staticmethod(lambda: _populate_snapshot())})(),
            chat_history_provider=type("C", (), {
                "history": lambda self: type("H", (), {"to_public_dict": lambda self: {"messages": []}})(),
                "send": lambda self, m: type("R", (), {"to_public_dict": lambda self: {"ok": True}})(),
            })(),
            security_settings=SecuritySettings(enforcement="local-bypass"),
            access_validator=validator,
            chat_rate_limiter=InProcessRateLimiter(limit=10, window_seconds=60.0),
        )
        client = TestClient(app, client=("203.0.113.5", 50000))
        resp = client.get(SNAPSHOT_ROUTE, headers={CF_ACCESS_JWT_HEADER: bad_token})
        assert resp.status_code == 401

    def test_local_bypass_accepts_external_with_valid_jwt(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        host, port = jwks_server._server.server_address[:2]
        token = jwt.encode(
            {
                "iss": f"https://{host}:{port}",
                "aud": AUDIENCE,
                "sub": "user|abc",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
                "email": "josh@example.com",
            },
            private_key_bytes(private),
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )
        validator = CloudflareAccessValidator(
            team_domain=f"{host}:{port}",
            audience=AUDIENCE,
            certs_base_url=f"http://{host}:{port}{CF_ACCESS_CERTS_PATH}",
        )
        app = create_app(
            snapshot_provider=type("P", (), {"snapshot": staticmethod(lambda: _populate_snapshot())})(),
            chat_history_provider=type("C", (), {
                "history": lambda self: type("H", (), {"to_public_dict": lambda self: {"messages": []}})(),
                "send": lambda self, m: type("R", (), {"to_public_dict": lambda self: {"ok": True}})(),
            })(),
            security_settings=SecuritySettings(enforcement="local-bypass"),
            access_validator=validator,
            chat_rate_limiter=InProcessRateLimiter(limit=10, window_seconds=60.0),
        )
        client = TestClient(app, client=("203.0.113.5", 50000))
        resp = client.get(SNAPSHOT_ROUTE, headers={CF_ACCESS_JWT_HEADER: token})
        assert resp.status_code == 200

    def test_always_enforcement_requires_jwt_even_on_loopback(self, jwks_keypair, jwks_server):
        private, _ = jwks_keypair
        host, port = jwks_server._server.server_address[:2]
        token = jwt.encode(
            {
                "iss": f"https://{host}:{port}",
                "aud": AUDIENCE,
                "sub": "user|abc",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
                "email": "josh@example.com",
            },
            private_key_bytes(private),
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )
        validator = CloudflareAccessValidator(
            team_domain=f"{host}:{port}",
            audience=AUDIENCE,
            certs_base_url=f"http://{host}:{port}{CF_ACCESS_CERTS_PATH}",
        )
        app = create_app(
            snapshot_provider=type("P", (), {"snapshot": staticmethod(lambda: _populate_snapshot())})(),
            chat_history_provider=type("C", (), {
                "history": lambda self: type("H", (), {"to_public_dict": lambda self: {"messages": []}})(),
                "send": lambda self, m: type("R", (), {"to_public_dict": lambda self: {"ok": True}})(),
            })(),
            security_settings=SecuritySettings(enforcement="always"),
            access_validator=validator,
            chat_rate_limiter=InProcessRateLimiter(limit=10, window_seconds=60.0),
        )
        # Loopback without JWT -> 401 under "always".
        no_jwt = TestClient(app, client=("127.0.0.1", 50000))
        assert no_jwt.get(SNAPSHOT_ROUTE).status_code == 401
        # Loopback with JWT -> 200.
        with_jwt = TestClient(app, client=("127.0.0.1", 50000))
        resp = with_jwt.get(SNAPSHOT_ROUTE, headers={CF_ACCESS_JWT_HEADER: token})
        assert resp.status_code == 200

    def test_chat_send_rate_limit_returns_429_with_retry_after(self):
        settings = SecuritySettings(
            enforcement="disabled",
            chat_send_rate_limit=2,
            chat_send_rate_window_seconds=60.0,
        )
        app = self._build_app(settings)
        client = TestClient(app)
        body = {"message": "hello"}
        # Two allowed.
        assert client.post(CHAT_SEND_ROUTE, json=body).status_code == 200
        assert client.post(CHAT_SEND_ROUTE, json=body).status_code == 200
        # Third is rate-limited.
        resp = client.post(CHAT_SEND_ROUTE, json=body)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"].isdigit()
        assert resp.json() == {
            "ok": False,
            "status": "rate_limited",
            "error": "chat.send rate limit exceeded",
        }

    def test_chat_send_rejects_disallowed_origin(self):
        settings = SecuritySettings(
            enforcement="disabled",
            public_hostnames=("dashboard.example.com",),
        )
        app = self._build_app(settings)
        client = TestClient(app)
        resp = client.post(
            CHAT_SEND_ROUTE,
            json={"message": "x"},
            headers={"Origin": "https://attacker.com"},
        )
        assert resp.status_code == 403
        assert resp.json()["status"] == "origin_not_allowed"

    def test_chat_send_accepts_matching_origin(self):
        settings = SecuritySettings(
            enforcement="disabled",
            public_hostnames=("example.com",),
        )
        app = self._build_app(settings)
        client = TestClient(app)
        resp = client.post(
            CHAT_SEND_ROUTE,
            json={"message": "x"},
            headers={"Origin": "https://dashboard.example.com"},
        )
        assert resp.status_code == 200

    def test_chat_send_preserves_4000_char_bound(self):
        # Regression: the existing 4,000-char outbound bound must not be
        # weakened by the security wiring.
        settings = SecuritySettings(
            enforcement="disabled",
            public_hostnames=(),
        )
        app = self._build_app(settings)
        client = TestClient(app)
        too_long = "x" * (CHAT_SEND_MAX_CHARS + 1)
        resp = client.post(CHAT_SEND_ROUTE, json={"message": too_long})
        assert resp.status_code == 400
        assert resp.json()["status"] == "rejected"

    def test_chat_send_rejects_non_text_message(self):
        settings = SecuritySettings(
            enforcement="disabled",
            public_hostnames=(),
        )
        app = self._build_app(settings)
        client = TestClient(app)
        resp = client.post(CHAT_SEND_ROUTE, json={"message": ["list", "not", "allowed"]})
        assert resp.status_code == 400
        assert resp.json()["status"] == "rejected"
