from __future__ import annotations

from unittest.mock import patch

from obs.config import SecuritySettings, Settings, override_settings
from obs.security.url_targets import add_allowed_url_target, evaluate_url_target, list_allowed_url_targets, remove_allowed_url_target


def _settings_for(path) -> Settings:
    return Settings(security=SecuritySettings(jwt_secret="unit-test-secret-32-chars-xxx", url_target_allowlist_path=str(path)))


def test_private_ip_is_blocked_without_allowlist(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://10.38.113.23/api/v1/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["10.38.113.23"]
    assert decision.suggested_target == "10.38.113.23/32"


def test_private_ip_is_allowed_by_yaml_allowlist(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.23", reason="internal test target", created_by="admin")

    decision = evaluate_url_target("http://10.38.113.23/api/v1/status")

    assert decision.allowed is True
    assert decision.allowlisted_by == "10.38.113.23/32"
    entries = list_allowed_url_targets()
    assert entries[0].target == "10.38.113.23/32"
    assert entries[0].reason == "internal test target"


def test_hostname_resolving_to_private_ip_uses_allowlisted_network(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.0/24", reason="internal subnet", created_by="admin")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.resolved_ips == ["10.38.113.23"]
    assert decision.allowlisted_by == "10.38.113.0/24"


def test_remove_allowlist_entry(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.23", reason="", created_by="admin")

    assert remove_allowed_url_target("10.38.113.23/32") is True
    assert list_allowed_url_targets() == []
