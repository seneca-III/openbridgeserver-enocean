from __future__ import annotations

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal, get_admin_user, get_current_principal, get_current_user


class _DbStub:
    def __init__(self, *, api_key_row: dict | None = None, user_is_admin: int | None = None) -> None:
        self.api_key_row = api_key_row
        self.user_is_admin = user_is_admin
        self.updated = False

    async def fetchone(self, query: str, params: tuple):
        if "FROM api_keys" in query:
            return self.api_key_row
        if "FROM users" in query:
            if self.user_is_admin is None:
                return None
            return {"is_admin": self.user_is_admin}
        raise AssertionError(f"Unexpected query: {query} params={params}")

    async def execute_and_commit(self, _query: str, _params: tuple) -> None:
        self.updated = True


@pytest.mark.asyncio
async def test_get_current_principal_user_has_type_subject_and_admin(monkeypatch):
    monkeypatch.setattr("obs.api.auth.decode_token", lambda token: "alice")
    db = _DbStub(user_is_admin=1)

    principal = await get_current_principal(credentials=type("Cred", (), {"credentials": "jwt"})(), api_key=None, db=db)

    assert principal == Principal(subject="alice", type="user", is_admin=True)


@pytest.mark.asyncio
async def test_get_current_principal_api_key_uses_key_name_not_owner(monkeypatch):
    monkeypatch.setattr("obs.api.auth.hash_api_key", lambda key: f"hash:{key}")
    db = _DbStub(api_key_row={"name": "ci-key", "owner": "admin"})

    principal = await get_current_principal(credentials=None, api_key="obs_valid", db=db)

    assert principal == Principal(subject="ci-key", type="api_key", is_admin=False)
    assert db.updated is True


@pytest.mark.asyncio
async def test_get_current_user_keeps_legacy_string_for_user_principal(monkeypatch):
    async def _principal(*_args, **_kwargs):
        return Principal(subject="alice", type="user", is_admin=False)

    monkeypatch.setattr(
        "obs.api.auth.get_current_principal",
        _principal,
    )

    user = await get_current_user(credentials=None, api_key=None, db=None)

    assert user == "alice"


@pytest.mark.asyncio
async def test_get_admin_user_rejects_api_key_even_with_admin_flag():
    principal = Principal(subject="ci-key", type="api_key", is_admin=True)

    with pytest.raises(HTTPException) as exc:
        await get_admin_user(principal=principal)

    assert exc.value.status_code == 403
    assert "Admin" in exc.value.detail
