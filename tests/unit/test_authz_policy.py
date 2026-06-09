from __future__ import annotations

from obs.api.auth import Principal
from obs.api.authz import AuthzAction, AuthzTarget, GrantEffect, Role, RoleGrant, authorize


def _user(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


def _grant(
    node_id: str,
    *,
    role: Role = Role.RESIDENT,
    effect: GrantEffect = GrantEffect.ALLOW,
    ancestors: tuple[str, ...] = (),
) -> RoleGrant:
    return RoleGrant(
        principal_type="user",
        principal_id="alice",
        node_type="hierarchy",
        node_id=node_id,
        role=role,
        effect=effect,
        ancestors=ancestors,
    )


def _target(node_id: str, *, ancestors: tuple[str, ...] = (), min_role: Role | None = None) -> AuthzTarget:
    return AuthzTarget(node_type="hierarchy", node_id=node_id, ancestors=ancestors, min_role=min_role)


def test_admin_user_is_allowed_without_grants():
    decision = authorize(
        principal=_user(is_admin=True),
        action=AuthzAction.WRITE,
        targets=[_target("room")],
        grants=[],
    )

    assert decision.allowed is True
    assert decision.reason == "admin"


def test_no_targets_is_denied():
    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[], grants=[])

    assert decision.allowed is False
    assert decision.reason == "no_targets"


def test_guest_can_read_but_not_write():
    target = _target("room")
    grant = _grant("room", role=Role.GUEST)

    read = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])
    write = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert read.allowed is True
    assert write.allowed is False
    assert write.reason == "missing_allow"


def test_deny_beats_matching_allow():
    target = _target("room")
    grants = [
        _grant("room", role=Role.OWNER),
        _grant("room", role=Role.GUEST, effect=GrantEffect.DENY),
    ]

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=grants)

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_read_inherits_upwards_from_assigned_child_node():
    grant = _grant("room", role=Role.GUEST, ancestors=("building", "floor"))
    target = _target("floor", ancestors=("building",))

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])

    assert decision.allowed is True


def test_write_inherits_downwards_from_assigned_parent_node():
    grant = _grant("floor", role=Role.RESIDENT, ancestors=("building",))
    target = _target("room", ancestors=("building", "floor"))

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert decision.allowed is True


def test_write_does_not_inherit_upwards_from_child_node():
    grant = _grant("room", role=Role.OWNER, ancestors=("building", "floor"))
    target = _target("floor", ancestors=("building",))

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_read_uses_any_target_semantics():
    allowed = _target("room-a")
    missing = _target("room-b")
    grant = _grant("room-a", role=Role.GUEST)

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[allowed, missing], grants=[grant])

    assert decision.allowed is True


def test_read_without_matching_grant_is_denied():
    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[_target("room")], grants=[])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_grants_do_not_cross_node_types():
    target = AuthzTarget(node_type="visu", node_id="room")
    grant = _grant("room", role=Role.GUEST)

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_write_uses_all_target_semantics():
    allowed = _target("room-a")
    missing = _target("room-b")
    grant = _grant("room-a", role=Role.RESIDENT)

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[allowed, missing], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_control_class_gate_requires_minimum_role():
    target = _target("actuator", min_role=Role.OPERATOR)
    resident = _grant("actuator", role=Role.RESIDENT)
    operator = _grant("actuator", role=Role.OPERATOR)

    resident_decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[resident])
    operator_decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[operator])

    assert resident_decision.allowed is False
    assert operator_decision.allowed is True
