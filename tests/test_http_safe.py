import socket
import unittest
from unittest.mock import MagicMock, patch

from job_search.http_safe import (
    RedirectLimitExceeded, RedirectLoop, RequestBudget, UnsafeDestination,
    safe_http_get, validate_outbound_url,
)


PUBLIC_V4 = "8.8.8.8"


class FakeResponse:
    def __init__(self, status=200, body=b"ok", location=None):
        self.status = status
        self._body = body
        self._location = location

    def getheader(self, name):
        return self._location if name.lower() == "location" else None

    def read(self, _limit):
        return self._body


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requested = False

    def request(self, *_args, **_kwargs):
        self.requested = True

    def getresponse(self):
        return self.response

    def close(self):
        pass


def public_dns(_host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, port))]


class OutboundSafetyTests(unittest.TestCase):
    def test_rejects_non_http_and_embedded_credentials(self):
        for url in (
            "file:///etc/passwd", "ftp://example.com/job", "javascript:alert(1)",
            "data:text/plain,hello", "https://user:password@example.com/job",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeDestination):
                validate_outbound_url(url)

    def test_rejects_local_private_link_local_and_reserved_addresses(self):
        urls = (
            "http://localhost/job", "http://127.0.0.1/job", "http://127.9.8.7/job",
            "http://10.0.0.1/job", "http://172.16.0.1/job", "http://192.168.1.1/job",
            "http://169.254.169.254/latest/meta-data", "http://0.0.0.0/job",
            "http://[::1]/job", "http://[fc00::1]/job", "http://[fe80::1]/job",
            "http://[ff02::1]/job", "http://[::]/job",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(UnsafeDestination):
                validate_outbound_url(url)

    @patch("job_search.http_safe.socket.getaddrinfo")
    def test_rejects_hostname_if_any_resolved_address_is_not_global(self, resolver):
        resolver.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]
        with self.assertRaises(UnsafeDestination):
            validate_outbound_url("https://mixed.example/job")

    @patch("job_search.http_safe._pinned_connection")
    @patch("job_search.http_safe.socket.getaddrinfo", side_effect=public_dns)
    def test_public_to_public_redirect_is_explicitly_followed(self, _resolver, pinned):
        first = FakeConnection(FakeResponse(302, location="https://apply.example/job/1"))
        second = FakeConnection(FakeResponse(200, b"apply now"))
        pinned.side_effect = [first, second]
        budget = RequestBudget(10)
        response = safe_http_get("https://jobs.example/job/1", headers={}, budget=budget)
        self.assertEqual(response.final_hostname, "apply.example")
        self.assertEqual(budget.requests_attempted, 2)
        self.assertEqual(budget.redirects_followed, 1)

    @patch("job_search.http_safe._pinned_connection")
    @patch("job_search.http_safe.socket.getaddrinfo", side_effect=public_dns)
    def test_unsafe_redirect_targets_are_never_requested(self, _resolver, pinned):
        targets = (
            "http://localhost/job", "http://127.0.0.1/job", "http://10.0.0.1/job",
            "http://169.254.169.254/latest", "http://[::1]/job", "http://[fc00::1]/job",
            "file:///etc/passwd", "https://user:pass@example.com/job",
        )
        for target in targets:
            with self.subTest(target=target):
                first = FakeConnection(FakeResponse(302, location=target))
                pinned.reset_mock()
                pinned.return_value = first
                budget = RequestBudget(10)
                with self.assertRaises(UnsafeDestination):
                    safe_http_get("https://jobs.example/job/1", headers={}, budget=budget)
                self.assertEqual(pinned.call_count, 1)
                self.assertEqual(budget.requests_attempted, 1)

    @patch("job_search.http_safe._pinned_connection")
    @patch("job_search.http_safe.socket.getaddrinfo", side_effect=public_dns)
    def test_redirect_loop_is_detected(self, _resolver, pinned):
        pinned.return_value = FakeConnection(FakeResponse(302, location="https://jobs.example/job/1"))
        with self.assertRaises(RedirectLoop):
            safe_http_get("https://jobs.example/job/1", headers={}, budget=RequestBudget(10))

    @patch("job_search.http_safe._pinned_connection")
    @patch("job_search.http_safe.socket.getaddrinfo", side_effect=public_dns)
    def test_redirect_budget_is_enforced(self, _resolver, pinned):
        pinned.return_value = FakeConnection(FakeResponse(302, location="https://next.example/job"))
        with self.assertRaises(RedirectLimitExceeded):
            safe_http_get(
                "https://jobs.example/job", headers={}, budget=RequestBudget(10), maximum_redirects=0,
            )

    @patch("job_search.http_safe.socket.create_connection")
    @patch("job_search.http_safe.socket.getaddrinfo", side_effect=public_dns)
    def test_connection_is_pinned_to_the_validated_address(self, _resolver, create_connection):
        target = validate_outbound_url("http://jobs.example/job")
        from job_search.http_safe import _pinned_connection
        connection = _pinned_connection(target, 12)
        connection._create_connection(("untrusted-second-resolution", 80), 12, None)
        create_connection.assert_called_once_with((PUBLIC_V4, 80), 12, None)


if __name__ == "__main__":
    unittest.main()
