"""Security primitives for the Engineering Dashboard (PR2).

This module provides the three security layers required to expose the
dashboard to the public internet via a Cloudflare Tunnel protected by
Cloudflare Access:

1. CloudflareAccessValidator — server-side validation of
   ``Cf-Access-Jwt-Assertion``. Does NOT trust the mere presence of the
   header. Validates signature against Cloudflare's published certs and
   checks issuer, audience, and expiry. Never logs the JWT or its
   claims.

2. WriteOriginGuard — verifies the ``Origin`` (or fallback ``Referer``)
   of mutating requests matches the configured public hostname(s).

3. InProcessRateLimiter — bounded sliding-window rate limiter for the
   single mutable write route (``/api/engineering/chat/send``).

4. Localhost health bypass — the ``/healthz`` endpoint is reachable only
   from ``127.0.0.1``. It deliberately bypasses Access so that local
   health checks, ssh scripts, and one-shot tests do not require a JWT.

The dashboard enforces access via a single config knob,
``DASHBOARD_CF_ACCESS_ENFORCEMENT``:

- ``disabled`` — never validate (legacy / unit tests / trusted LAN).
- ``local-bypass`` (default for production) — validate on every request
  that is NOT from 127.0.0.1; requests from 127.0.0.1 without a JWT are
  treated as direct loopback and pass through to the app logic.
- ``always`` — validate on every request including 127.0.0.1 (useful
  for staging before tunnel is wired up).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Iterable, Mapping, Sequence

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Request
from fastapi.responses import JSONResponse
from jwt import PyJWKClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cloudflare Access JWT validation
# ---------------------------------------------------------------------------


#: Standard header name set by Cloudflare Access on every authenticated
#: request that flows through the Access JWT validation chain.
CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"

#: Standard header name set by Cloudflare Access for the (plain) email of
#: the authenticated user. We deliberately do NOT trust this header for
#: authorisation; we read the email from the verified JWT claims only.
CF_ACCESS_USER_HEADER = "Cf-Access-Authenticated-User-Email"

#: Standard HTTP header Cloudflare sets to the original client IP. Used
#: for rate-limit keying (so the rate limit survives the tunnel
#: rewriting ``request.client.host`` to 127.0.0.1).
CF_CONNECTING_IP_HEADER = "Cf-Connecting-Ip"

#: Default Cloudflare Access certs endpoint for a team. Resolved per
#: team via ``DASHBOARD_CF_ACCESS_TEAM_DOMAIN``.
CF_ACCESS_CERTS_PATH = "/cdn-cgi/access/certs"

#: Maximum age (seconds) of a cached JWKS document before we re-fetch.
DEFAULT_JWKS_CACHE_SECONDS = 3600

#: Maximum total number of JWKS documents we will cache before evicting.
DEFAULT_JWKS_CACHE_MAX_ENTRIES = 32

#: Allow list of JWT signing algorithms. Cloudflare Access uses RS256.
ALLOWED_JWT_ALGORITHMS: tuple[str, ...] = ("RS256",)

#: Bounded string used in error paths so logs are deterministic and
#: never leak the JWT or any claim value.
JWT_STATUS_VALID = "valid"
JWT_STATUS_MISSING = "missing"
JWT_STATUS_BAD_FORMAT = "bad_format"
JWT_STATUS_BAD_SIGNATURE = "bad_signature"
JWT_STATUS_EXPIRED = "expired"
JWT_STATUS_BAD_ISSUER = "bad_issuer"
JWT_STATUS_BAD_AUDIENCE = "bad_audience"
JWT_STATUS_BAD_ALGORITHM = "bad_algorithm"
JWT_STATUS_CERTS_UNAVAILABLE = "certs_unavailable"


@dataclass(frozen=True)
class JwtValidationResult:
    """Bounded outcome of a Cloudflare Access JWT validation attempt.

    Carries only booleans and bounded enums — never the JWT itself, the
    raw claims, or any PII. ``email`` is the bounded subject claim (an
    email address) and is the ONLY identity-shaped field exposed to
    callers. The audit logger emits only the status enum and the email.
    """

    valid: bool
    status: str
    email: str | None = None


class CloudflareAccessValidator:
    """Validates Cloudflare Access JWTs server-side.

    The validator caches JWKS documents per team domain for a bounded
    TTL so the live certs endpoint is not hammered on every request.
    The certs endpoint is fetched over HTTPS using PyJWT's built-in
    :class:`jwt.PyJWKClient` which is responsible for the network fetch
    and any per-key cache.

    Configuration via constructor (callers may pass the result of
    ``Settings.from_env()`` to keep this module pure)::

        CloudflareAccessValidator(
            team_domain="myteam.cloudflareaccess.com",
            audience="<application-aud-tag>",
        )

    If either ``team_domain`` or ``audience`` is empty, every validation
    attempt returns ``JwtValidationResult(valid=False, status=...,
    email=None)`` with a deterministic status. This makes the validator
    safe to construct at app startup before the env has been wired up.
    """

    def __init__(
        self,
        *,
        team_domain: str,
        audience: str,
        jwks_cache_seconds: int = DEFAULT_JWKS_CACHE_SECONDS,
        jwks_client_factory: Callable[[str, int], PyJWKClient] | None = None,
        certs_base_url: str | None = None,
    ) -> None:
        self._team_domain = (team_domain or "").strip()
        self._audience = (audience or "").strip()
        self._jwks_cache_seconds = max(60, int(jwks_cache_seconds))
        self._jwks_client_factory = jwks_client_factory or self._default_jwks_client
        # Production builds the certs URL as
        # ``https://{team_domain}/cdn-cgi/access/certs``. Tests can
        # override the base URL to point at an ``http://localhost:port``
        # fixture without needing a real Cloudflare team.
        self._certs_base_url_override = (certs_base_url or "").strip() or None
        self._jwks_clients: dict[str, tuple[PyJWKClient, float]] = {}
        self._jwks_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """True iff both ``team_domain`` and ``audience`` are set."""
        return bool(self._team_domain) and bool(self._audience)

    def validate(self, jwt_token: str | None) -> JwtValidationResult:
        """Validate a JWT and return a bounded result.

        Never logs the JWT itself or its claims. The status enum and
        the verified email (if any) are the only fields exposed to
        callers.
        """
        if not self.is_configured:
            return JwtValidationResult(False, JWT_STATUS_CERTS_UNAVAILABLE)
        if not jwt_token:
            return JwtValidationResult(False, JWT_STATUS_MISSING)
        token = jwt_token.strip()
        if not token:
            return JwtValidationResult(False, JWT_STATUS_MISSING)

        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            return JwtValidationResult(False, JWT_STATUS_BAD_FORMAT)
        alg = unverified_header.get("alg")
        if alg not in ALLOWED_JWT_ALGORITHMS:
            return JwtValidationResult(False, JWT_STATUS_BAD_ALGORITHM)

        try:
            signing_key = self._get_signing_key(token)
        except Exception:  # noqa: BLE001 — bounded status only.
            return JwtValidationResult(False, JWT_STATUS_CERTS_UNAVAILABLE)

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(ALLOWED_JWT_ALGORITHMS),
                audience=self._audience,
                issuer=f"https://{self._team_domain}",
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError:
            return JwtValidationResult(False, JWT_STATUS_EXPIRED)
        except jwt.InvalidIssuerError:
            return JwtValidationResult(False, JWT_STATUS_BAD_ISSUER)
        except jwt.InvalidAudienceError:
            return JwtValidationResult(False, JWT_STATUS_BAD_AUDIENCE)
        except jwt.InvalidSignatureError:
            return JwtValidationResult(False, JWT_STATUS_BAD_SIGNATURE)
        except jwt.InvalidAlgorithmError:
            return JwtValidationResult(False, JWT_STATUS_BAD_ALGORITHM)
        except jwt.InvalidTokenError:
            return JwtValidationResult(False, JWT_STATUS_BAD_FORMAT)

        email = claims.get("email")
        if not isinstance(email, str) or not email:
            # Some IdPs use a non-email subject. Fall back to a
            # bounded string of the subject (truncated).
            sub = claims.get("sub")
            if isinstance(sub, str) and sub:
                email = sub[:128]
            else:
                email = None

        return JwtValidationResult(True, JWT_STATUS_VALID, email=email)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _certs_url(self) -> str:
        if self._certs_base_url_override:
            return self._certs_base_url_override
        return f"https://{self._team_domain}{CF_ACCESS_CERTS_PATH}"

    def _get_signing_key(self, token: str):
        client = self._get_or_create_jwks_client(self._certs_url())
        return client.get_signing_key_from_jwt(token)

    def _get_or_create_jwks_client(self, url: str) -> PyJWKClient:
        now = time.monotonic()
        with self._jwks_lock:
            cached = self._jwks_clients.get(url)
            if cached is not None:
                client, expires_at = cached
                if expires_at > now:
                    return client
        client = self._jwks_client_factory(url, self._jwks_cache_seconds)
        with self._jwks_lock:
            self._evict_jwks_cache_if_needed()
            self._jwks_clients[url] = (client, now + self._jwks_cache_seconds)
        return client

    def _evict_jwks_cache_if_needed(self) -> None:
        if len(self._jwks_clients) < DEFAULT_JWKS_CACHE_MAX_ENTRIES:
            return
        # Evict the entry closest to expiry.
        oldest_url = min(
            self._jwks_clients,
            key=lambda k: self._jwks_clients[k][1],
        )
        self._jwks_clients.pop(oldest_url, None)

    @staticmethod
    def _default_jwks_client(url: str, cache_seconds: int) -> PyJWKClient:
        # PyJWKClient caches per-URL keys internally; we additionally
        # bound the lifetime of the whole client object via
        # ``cache_seconds``.
        return PyJWKClient(url, cache_keys=True, lifespan=cache_seconds)


def build_validator_from_public_key(public_key: RSAPublicKey, audience: str) -> jwt.algorithms.AllowedPublicKeys:
    """Test helper: build a PyJWT-compatible public key from an
    :class:`RSAPublicKey` returned by ``cryptography``.
    """
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return jwt.algorithms.RSAAlgorithm.from_jwk(pem.decode("ascii"))


# ---------------------------------------------------------------------------
# Origin / Referer enforcement for mutating requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OriginCheckResult:
    allowed: bool
    reason: str = "ok"


class WriteOriginGuard:
    """Allows mutating requests whose ``Origin`` (or fallback ``Referer``)
    matches one of the configured public hostnames.

    Same-origin requests from a configured hostname are accepted.
    Requests without an Origin or Referer are REJECTED for write
    methods unless ``allow_missing_origin`` is set (which is needed for
    curl/Swagger-style direct hits). The hostname comparison is
    case-insensitive and matches the bare hostname or any subdomain of
    the configured value (so ``dashboard.example.com`` and
    ``www.dashboard.example.com`` both match ``example.com``).
    """

    _MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(
        self,
        *,
        public_hostnames: Sequence[str] | None = None,
        allow_missing_origin: bool = False,
    ) -> None:
        self._public_hostnames: tuple[str, ...] = tuple(
            h.strip().lower() for h in (public_hostnames or ()) if h and h.strip()
        )
        self._allow_missing_origin = bool(allow_missing_origin)

    @property
    def public_hostnames(self) -> tuple[str, ...]:
        return self._public_hostnames

    def should_check(self, method: str) -> bool:
        return method.upper() in self._MUTATING_METHODS

    def is_enabled(self) -> bool:
        """An empty hostname list means the guard is a no-op (dev mode).

        Tests and dev environments without a public hostname can opt to
        keep the guard as a no-op via this flag.
        """
        return bool(self._public_hostnames)

    def check(self, *, method: str, origin: str | None, referer: str | None) -> OriginCheckResult:
        if not self.should_check(method):
            return OriginCheckResult(True, "non_mutating")
        if not self.is_enabled():
            # Dev mode: no configured public hostnames => allow anything.
            return OriginCheckResult(True, "guard_disabled")
        candidate = (origin or "").strip()
        if not candidate and referer:
            candidate = referer.strip()
        if not candidate:
            if self._allow_missing_origin:
                return OriginCheckResult(True, "missing_origin_allowed")
            return OriginCheckResult(False, "missing_origin")
        host = _extract_host(candidate)
        if not host:
            return OriginCheckResult(False, "no_host_in_origin")
        host = host.lower()
        for allowed in self._public_hostnames:
            if host == allowed or host.endswith("." + allowed):
                return OriginCheckResult(True, "host_match")
        return OriginCheckResult(False, "host_mismatch")


def _extract_host(value: str) -> str:
    """Pull a hostname out of a URL-or-host string.

    Accepts:
      - bare hostnames ("dashboard.example.com")
      - origin URLs ("https://dashboard.example.com:443")
      - referer URLs ("https://dashboard.example.com/path?x=1")
    Returns an empty string for inputs that don't look URL-shaped
    (contain whitespace, missing scheme AND missing dot, or otherwise
    fail the loose URL-shape heuristic).
    """
    val = value.strip()
    if not val:
        return ""
    # Reject anything with whitespace — hostnames cannot contain it.
    if any(c.isspace() for c in val):
        return ""
    # It must either start with a scheme or contain a dot (bare hostname
    # like "dashboard.example.com"). This prevents accepting opaque
    # single tokens as hosts.
    if "://" not in val and "." not in val:
        return ""
    # Strip scheme.
    if "://" in val:
        _, _, after = val.partition("://")
        val = after
    # Drop userinfo.
    if "@" in val:
        _, _, after = val.partition("@")
        val = after
    # Take everything up to the next /, ?, #.
    for terminator in ("/", "?", "#"):
        if terminator in val:
            val = val.split(terminator, 1)[0]
            break
    # Drop :port.
    if ":" in val:
        val = val.split(":", 1)[0]
    val = val.strip()
    if not val or "." not in val:
        return ""
    return val


# ---------------------------------------------------------------------------
# Rate limiter (bounded sliding window)
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    timestamps: Deque[float] = field(default_factory=deque)


class InProcessRateLimiter:
    """Bounded sliding-window rate limiter for one process.

    Keyed by an opaque identity (e.g., the verified email or
    ``Cf-Connecting-Ip``). The total number of distinct keys is capped
    to ``max_keys``; the oldest key is evicted if that bound is reached.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        max_keys: int = 1024,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self._limit = max(1, int(limit))
        self._window = max(0.001, float(window_seconds))
        self._max_keys = max(1, int(max_keys))
        self._time_source = time_source or time.monotonic
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``.

        ``retry_after_seconds`` is 0 when ``allowed`` is True, and the
        number of seconds until the oldest timestamp in the window
        expires otherwise.
        """
        if not key:
            key = "__anon__"
        now = self._time_source()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket()
                self._evict_if_needed_locked()
                self._buckets[key] = bucket
            # Drop expired entries.
            while bucket.timestamps and bucket.timestamps[0] <= cutoff:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= self._limit:
                retry_after = max(1, int(bucket.timestamps[0] + self._window - now) + 1)
                return False, retry_after
            bucket.timestamps.append(now)
            return True, 0

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_if_needed_locked(self) -> None:
        if len(self._buckets) < self._max_keys:
            return
        # Evict the bucket whose newest timestamp is oldest.
        victim = min(
            self._buckets,
            key=lambda k: self._buckets[k].timestamps[-1] if self._buckets[k].timestamps else 0.0,
        )
        self._buckets.pop(victim, None)


# ---------------------------------------------------------------------------
# Common HTTP helpers
# ---------------------------------------------------------------------------


def request_is_from_loopback(request: Request) -> bool:
    """True iff ``request.client.host`` is 127.0.0.1 (loopback).

    Cloudflare Tunnel rewrites the client peer to 127.0.0.1 when it
    forwards a request to the origin. We therefore distinguish a true
    direct-loopback request from a tunnel-relayed request by checking
    whether the ``Cf-Connecting-Ip`` header is present (it is only set
    on tunneled requests). The loopback bypass applies only to direct
    requests (no ``Cf-Connecting-Ip``).
    """
    client = request.client
    if client is None:
        return False
    if client.host not in ("127.0.0.1", "::1", "localhost"):
        return False
    if request.headers.get(CF_CONNECTING_IP_HEADER):
        # Tunneled: not a true loopback.
        return False
    return True


def write_method(method: str) -> bool:
    return method.upper() in WriteOriginGuard._MUTATING_METHODS


def make_rejection(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"ok": False, "status": code, "error": message}
    return JSONResponse(body, status_code=status_code, headers=dict(headers or {}))


# ---------------------------------------------------------------------------
# Settings — collected here so security wiring is in one place
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecuritySettings:
    """Bounded security config derived from environment."""

    enforcement: str = "disabled"  # one of: disabled, local-bypass, always
    team_domain: str = ""
    audience: str = ""
    public_hostnames: tuple[str, ...] = ()
    allow_missing_origin: bool = False
    chat_send_rate_limit: int = 10
    chat_send_rate_window_seconds: float = 60.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SecuritySettings":
        env = environ if environ is not None else {}
        return cls(
            enforcement=(env.get("DASHBOARD_CF_ACCESS_ENFORCEMENT") or "disabled").strip().lower(),
            team_domain=(env.get("DASHBOARD_CF_ACCESS_TEAM_DOMAIN") or "").strip(),
            audience=(env.get("DASHBOARD_CF_ACCESS_AUDIENCE") or "").strip(),
            public_hostnames=tuple(
                h.strip().lower()
                for h in (env.get("DASHBOARD_PUBLIC_HOSTNAMES") or "").split(",")
                if h.strip()
            ),
            allow_missing_origin=(env.get("DASHBOARD_ALLOW_MISSING_ORIGIN") or "").strip().lower()
            in ("1", "true", "yes", "on"),
            chat_send_rate_limit=int(env.get("DASHBOARD_CHAT_SEND_RATE_LIMIT") or 10),
            chat_send_rate_window_seconds=float(env.get("DASHBOARD_CHAT_SEND_RATE_WINDOW_SECONDS") or 60.0),
        )

    def allowed_enforcement_values(self) -> frozenset[str]:
        return frozenset({"disabled", "local-bypass", "always"})

    def normalized_enforcement(self) -> str:
        en = self.enforcement if self.enforcement in self.allowed_enforcement_values() else "disabled"
        return en


__all__ = [
    "CF_ACCESS_JWT_HEADER",
    "CF_ACCESS_USER_HEADER",
    "CF_CONNECTING_IP_HEADER",
    "ALLOWED_JWT_ALGORITHMS",
    "JwtValidationResult",
    "JWT_STATUS_VALID",
    "JWT_STATUS_MISSING",
    "JWT_STATUS_BAD_FORMAT",
    "JWT_STATUS_BAD_SIGNATURE",
    "JWT_STATUS_EXPIRED",
    "JWT_STATUS_BAD_ISSUER",
    "JWT_STATUS_BAD_AUDIENCE",
    "JWT_STATUS_BAD_ALGORITHM",
    "JWT_STATUS_CERTS_UNAVAILABLE",
    "CloudflareAccessValidator",
    "OriginCheckResult",
    "WriteOriginGuard",
    "InProcessRateLimiter",
    "SecuritySettings",
    "request_is_from_loopback",
    "write_method",
    "make_rejection",
    "build_validator_from_public_key",
]
