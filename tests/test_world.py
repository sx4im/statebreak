"""Unit tests for authoritative in-memory LocalWorld."""

from __future__ import annotations

import pytest

from statebreak.clock import VirtualClock
from statebreak.errors import ConfigurationError
from statebreak.world import LocalWorld


def test_world_initialization_entities_list() -> None:
    world_config = {
        "entities": [
            {"id": "example-001", "type": "synthetic_record", "status": "pending"},
            {"id": "example-002", "type": "synthetic_record", "status": "active", "count": 10},
        ]
    }
    world = LocalWorld(world_config)
    assert world.has_entity("example-001")
    assert world.has_entity("example-002")
    assert not world.has_entity("example-999")

    ent1 = world.get_entity("example-001")
    assert ent1 is not None
    assert ent1["id"] == "example-001"
    assert ent1["status"] == "pending"
    assert ent1["version"] == "v1"

    # Deep copy check: mutating returned dict does not mutate internal world state
    ent1["status"] = "corrupted"
    ent1_fresh = world.get_entity("example-001")
    assert ent1_fresh is not None
    assert ent1_fresh["status"] == "pending"


def test_world_initialization_named_collections() -> None:
    world_config = {
        "approvals": [
            {
                "id": "appr-001",
                "subject": "refund-1001",
                "scope": "refund",
                "amount": 25.0,
                "expires_at": "2026-01-01T09:01:00Z",
                "status": "approved",
            }
        ],
        "refunds": [
            {"id": "refund-1001", "status": "pending", "amount": 25.0}
        ],
    }
    world = LocalWorld(world_config)
    assert world.has_entity("appr-001")
    assert world.has_entity("refund-1001")

    appr = world.get_entity("appr-001")
    assert appr is not None
    assert appr["type"] == "approval"
    assert appr["status"] == "approved"


def test_world_state_versioning_and_expected_version() -> None:
    world = LocalWorld({"entities": [{"id": "rec-01", "status": "pending"}]})
    assert world.get_entity_version("rec-01") == "v1"

    # Read does not increment version
    _ = world.get_entity("rec-01")
    assert world.get_entity_version("rec-01") == "v1"

    # Mutation with matching expected_version succeeds and increments version to v2
    res1 = world.update_entity(
        "rec-01",
        updates={"status": "processing"},
        expected_version="v1",
        operation_id="op_1",
    )
    assert res1.success is True
    assert res1.status == "committed"
    assert res1.before_version == "v1"
    assert res1.after_version == "v2"
    assert world.get_entity_version("rec-01") == "v2"

    # Mutation with stale expected_version is rejected and version remains v2
    res2 = world.update_entity(
        "rec-01",
        updates={"status": "completed"},
        expected_version="v1",  # Stale
        operation_id="op_2",
    )
    assert res2.success is False
    assert res2.status == "rejected"
    assert "version conflict" in (res2.error or "")
    assert world.get_entity_version("rec-01") == "v2"

    # Entity was not modified by rejected update
    ent = world.get_entity("rec-01")
    assert ent is not None
    assert ent["status"] == "processing"


def test_world_unknown_target_rejection() -> None:
    world = LocalWorld()
    res = world.update_entity(
        "non-existent",
        updates={"status": "active"},
        operation_id="op_unknown",
    )
    assert res.success is False
    assert res.status == "rejected"
    assert "does not exist" in (res.error or "")

    # Effect recorded as rejected
    effs = world.get_effects(target="non-existent")
    assert len(effs) == 1
    assert effs[0].status == "rejected"


def test_world_operation_id_and_duplicate_detection() -> None:
    world = LocalWorld({"entities": [{"id": "rec-01", "status": "pending", "counter": 0}]})

    res1 = world.update_entity(
        "rec-01",
        updates={"counter": 1},
        operation_id="op_idempotent_1",
    )
    assert res1.success is True
    assert res1.status == "committed"
    assert res1.after_version == "v2"

    # Duplicate operation ID submission does not re-apply mutation
    res2 = world.update_entity(
        "rec-01",
        updates={"counter": 2},
        operation_id="op_idempotent_1",  # Same op_id
    )
    assert res2.success is True
    assert res2.status == "committed"
    # Version remains v2
    assert world.get_entity_version("rec-01") == "v2"
    ent = world.get_entity("rec-01")
    assert ent is not None
    assert ent["counter"] == 1  # Not mutated to 2


def test_world_partial_write() -> None:
    world = LocalWorld({
        "entities": [
            {"id": "doc-01", "title": "Old Title", "body": "Old Body", "tags": ["draft"]}
        ]
    })

    res = world.partial_update_entity(
        entity_id="doc-01",
        updates={"title": "New Title", "body": "New Body", "tags": ["published"]},
        applied_fields=["title"],
        omitted_fields=["body", "tags"],
        operation_id="op_partial_1",
    )
    assert res.success is True
    assert res.status == "partial"
    assert res.before_version == "v1"
    assert res.after_version == "v2"
    assert res.applied_fields == ("title",)
    assert res.omitted_fields == ("body", "tags")

    ent = world.get_entity("doc-01")
    assert ent is not None
    assert ent["title"] == "New Title"
    assert ent["body"] == "Old Body"  # Omitted, not changed
    assert ent["tags"] == ["draft"]  # Omitted, not changed

    # Effect recorded with partial status
    eff = world.get_effect_by_operation("op_partial_1")
    assert eff is not None
    assert eff.status == "partial"


def test_world_approval_checking_and_expiry() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({
        "approvals": [
            {
                "id": "appr-valid",
                "subject": "ref-1",
                "amount": 50.0,
                "expires_at": "2026-01-01T09:01:00Z",
                "status": "approved",
            },
            {
                "id": "appr-revoked",
                "subject": "ref-2",
                "expires_at": "2026-01-01T09:10:00Z",
                "status": "revoked",
            },
        ]
    })

    # Initially valid
    obs1 = world.check_approval("appr-valid", clock)
    assert obs1.is_valid is True
    assert obs1.status == "approved"
    assert obs1.amount == 50.0

    # Non-existent approval
    obs_missing = world.check_approval("appr-missing", clock)
    assert obs_missing.is_valid is False
    assert obs_missing.status == "not_found"

    # Revoked approval
    obs_rev = world.check_approval("appr-revoked", clock)
    assert obs_rev.is_valid is False
    assert obs_rev.status == "revoked"

    # Advance clock to exact expiry boundary
    clock.advance(60)  # Now 09:01:00Z
    obs_exp = world.check_approval("appr-valid", clock)
    assert obs_exp.is_valid is False
    assert obs_exp.status == "expired"


def test_world_snapshot_determinism_and_sanitization() -> None:
    clock1 = VirtualClock("2026-01-01T09:00:00Z")
    clock2 = VirtualClock("2026-01-01T09:00:00Z")

    world_cfg = {
        "entities": [
            {"id": "b-entity", "status": "active"},
            {"id": "a-entity", "status": "pending"},
        ]
    }

    world1 = LocalWorld(world_cfg)
    world2 = LocalWorld(world_cfg)

    snap1 = world1.snapshot(clock1)
    snap2 = world2.snapshot(clock2)

    # Deterministic hashes across different world instances
    assert snap1.entities_hash == snap2.entities_hash
    assert snap1.snapshot_id == snap2.snapshot_id
    assert snap1.state_version == snap2.state_version
    assert snap1.captured_at == "2026-01-01T09:00:00Z"

    # Verify no host paths or addresses in snapshot
    snap_dict = {
        "snapshot_id": snap1.snapshot_id,
        "state_version": snap1.state_version,
        "captured_at": snap1.captured_at,
        "entities_hash": snap1.entities_hash,
    }
    for val in snap_dict.values():
        assert "/home/" not in val
        assert "0x" not in val


def test_world_clone_and_reset() -> None:
    world = LocalWorld({"entities": [{"id": "rec-01", "status": "pending"}]})
    world.update_entity("rec-01", {"status": "mutated"}, operation_id="op_1")
    assert world.get_entity_version("rec-01") == "v2"

    # Clone
    cloned = world.clone()
    assert cloned.get_entity_version("rec-01") == "v2"
    assert len(cloned.get_effects()) == 1

    # Mutating cloned does not mutate original
    cloned.update_entity("rec-01", {"status": "cloned_mutation"}, operation_id="op_2")
    assert cloned.get_entity_version("rec-01") == "v3"
    assert world.get_entity_version("rec-01") == "v2"

    # Reset original restores v1 and clears effects
    world.reset()
    assert world.get_entity_version("rec-01") == "v1"
    ent = world.get_entity("rec-01")
    assert ent is not None
    assert ent["status"] == "pending"
    assert len(world.get_effects()) == 0


def test_world_security_credential_rejection() -> None:
    with pytest.raises(ConfigurationError, match="security violation.*credential"):
        LocalWorld({"entities": [{"id": "bad-ent", "secret": "AKIAIOSFODNN7EXAMPLE"}]})


def test_world_list_entities_filtering() -> None:
    world = LocalWorld({
        "approvals": [{"id": "appr-1", "status": "approved"}],
        "refunds": [{"id": "ref-1", "status": "pending"}],
    })

    all_ents = world.list_entities()
    assert len(all_ents) == 2

    approvals = world.list_entities(entity_type="approval")
    assert len(approvals) == 1
    assert approvals[0]["id"] == "appr-1"
