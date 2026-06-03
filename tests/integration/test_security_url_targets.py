from __future__ import annotations


async def test_url_target_allowlist_admin_flow(client, auth_headers):
    blocked = await client.post(
        "/api/v1/security/url-target-check",
        json={"url": "http://10.38.113.23/api/v1/status"},
        headers=auth_headers,
    )
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["allowed"] is False
    assert blocked_body["suggested_target"] == "10.38.113.23/32"

    created = await client.post(
        "/api/v1/security/url-target-allowlist",
        json={"target": blocked_body["suggested_target"], "reason": "integration test"},
        headers=auth_headers,
    )
    assert created.status_code == 200
    assert created.json()["target"] == "10.38.113.23/32"

    allowed = await client.post(
        "/api/v1/security/url-target-check",
        json={"url": "http://10.38.113.23/api/v1/status"},
        headers=auth_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True

    listed = await client.get("/api/v1/security/url-target-allowlist", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["entries"][0]["target"] == "10.38.113.23/32"

    deleted = await client.delete(
        "/api/v1/security/url-target-allowlist",
        params={"target": "10.38.113.23/32"},
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
