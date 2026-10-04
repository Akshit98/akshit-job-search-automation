from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit


DEFAULT_TIMEOUT_SECONDS = 12
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2_000_000
MAX_VERIFICATION_JOBS = 100
MAX_HTTP_REQUESTS = 300
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UnsafeDestination(ValueError):
    pass


class RedirectLimitExceeded(RuntimeError):
    pass


class RedirectLoop(RuntimeError):
    pass


class RequestBudgetExhausted(RuntimeError):
    pass


class ResponseTooLarge(RuntimeError):
    pass


@dataclass(frozen=True)
class SafeTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class SafeResponse:
    status: int
    body: str
    final_url: str
    final_hostname: str
    redirects: int


class RequestBudget:
    def __init__(self, maximum_requests: int = MAX_HTTP_REQUESTS):
        self.maximum_requests = maximum_requests
        self.requests_attempted = 0
        self.redirects_followed = 0
        self.unsafe_destinations = 0
        self._lock = threading.Lock()

    def consume_request(self) -> None:
        with self._lock:
            if self.requests_attempted >= self.maximum_requests:
                raise RequestBudgetExhausted("verification request budget exhausted")
            self.requests_attempted += 1

    def record_redirect(self) -> None:
        with self._lock:
            self.redirects_followed += 1

    def record_unsafe(self) -> None:
        with self._lock:
            self.unsafe_destinations += 1


def _address_is_public(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def validate_outbound_url(url: str) -> SafeTarget:
    """Resolve and validate every address before any socket is opened."""
    try:
        parsed = urlsplit(str(url or "").strip())
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (TypeError, ValueError):
        raise UnsafeDestination("malformed destination") from None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeDestination("unsupported destination scheme")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeDestination("embedded credentials are not allowed")
    if not parsed.hostname:
        raise UnsafeDestination("destination hostname is missing")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise UnsafeDestination("malformed destination hostname") from None
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeDestination("non-public destination")
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        addresses = (str(literal),)
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError):
            raise UnsafeDestination("destination could not be safely resolved") from None
        addresses = tuple(dict.fromkeys(record[4][0] for record in records if record[4]))
    if not addresses or not all(_address_is_public(address) for address in addresses):
        raise UnsafeDestination("non-public destination")
    netloc = hostname
    if ":" in hostname:
        netloc = f"[{hostname}]"
    if port != (443 if scheme == "https" else 80):
        netloc += f":{port}"
    normalized = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    return SafeTarget(normalized, scheme, hostname, port, addresses)


def _pinned_connection(target: SafeTarget, timeout: int):
    connection_class = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": timeout}
    if target.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    connection = connection_class(target.hostname, target.port, **kwargs)
    validated_address = target.addresses[0]

    def create_connection(_address, timeout=timeout, source_address=None):
        return socket.create_connection((validated_address, target.port), timeout, source_address)

    # HTTPConnection and HTTPSConnection both use this hook. HTTPS still wraps
    # the pinned socket with target.hostname as SNI/certificate hostname.
    connection._create_connection = create_connection
    return connection


def safe_http_get(
    url: str,
    *,
    headers: dict[str, str],
    budget: RequestBudget,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    maximum_bytes: int = MAX_RESPONSE_BYTES,
    maximum_redirects: int = MAX_REDIRECTS,
    on_validated: Callable[[str, int], None] | None = None,
) -> SafeResponse:
    current = url
    visited: set[str] = set()
    redirects = 0
    while True:
        try:
            target = validate_outbound_url(current)
        except UnsafeDestination:
            budget.record_unsafe()
            raise
        if on_validated is not None:
            on_validated(target.hostname, redirects)
        if target.url in visited:
            raise RedirectLoop("redirect loop")
        visited.add(target.url)
        budget.consume_request()
        connection = _pinned_connection(target, timeout)
        try:
            parsed = urlsplit(target.url)
            request_target = parsed.path or "/"
            if parsed.query:
                request_target += f"?{parsed.query}"
            connection.request("GET", request_target, headers=headers)
            response = connection.getresponse()
            if response.status in REDIRECT_STATUSES and response.getheader("Location"):
                if redirects >= maximum_redirects:
                    raise RedirectLimitExceeded("redirect budget exceeded")
                current = urljoin(target.url, response.getheader("Location"))
                redirects += 1
                budget.record_redirect()
                continue
            payload = response.read(maximum_bytes + 1)
            if len(payload) > maximum_bytes:
                raise ResponseTooLarge("response exceeded size limit")
            return SafeResponse(
                status=response.status,
                body=payload.decode("utf-8", errors="ignore"),
                final_url=target.url,
                final_hostname=target.hostname,
                redirects=redirects,
            )
        finally:
            connection.close()
