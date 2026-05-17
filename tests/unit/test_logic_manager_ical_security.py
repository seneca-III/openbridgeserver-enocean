from __future__ import annotations

import socket

from obs.logic.manager import _is_public_http_url


def test_is_public_http_url_blocks_non_http_scheme() -> None:
    assert _is_public_http_url("file:///etc/passwd") is False


def test_is_public_http_url_blocks_loopback(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("http://localhost/calendar.ics") is False


def test_is_public_http_url_allows_public_ip(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("https://example.com/calendar.ics") is True
