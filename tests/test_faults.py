"""Unit tests for FaultScheduler declaration, ordering, and event generation."""

from __future__ import annotations

import pytest

from statebreak.clock import VirtualClock
from statebreak.errors import ConfigurationError
from statebreak.faults import FaultEvent, FaultScheduler
from statebreak.models import FaultSpec
from statebreak.world import LocalWorld


def test_fault_scheduler_valid_construction() -> None:
    faults = (
        FaultSpec(id="f1", at="before_commit", type="approval_expired", target="appr-1"),
        FaultSpec(id="f2", at="after_read", type="stale_read", target="rec-1", repeat=2),
    )
    scheduler = FaultScheduler(faults)
    assert scheduler.get_fault("f1") is not None
    assert scheduler.get_fault("f2") is not None
    assert scheduler.get_fault("f_missing") is None


def test_fault_scheduler_duplicate_id_rejection() -> None:
    faults = (
        FaultSpec(id="dup-id", at="before_commit", type="approval_expired"),
        FaultSpec(id="dup-id", at="after_read", type="stale_read"),
    )
    with pytest.raises(ConfigurationError, match="duplicate fault ID 'dup-id'"):
        FaultScheduler(faults)


def test_fault_scheduler_invalid_lifecycle_point_rejection() -> None:
    faults = (
        FaultSpec(id="f1", at="invalid_point", type="approval_expired"),
    )
    with pytest.raises(ConfigurationError, match="unknown fault lifecycle point 'invalid_point'"):
        FaultScheduler(faults)


def test_fault_scheduler_invalid_type_rejection() -> None:
    faults = (
        FaultSpec(id="f1", at="before_commit", type="unknown_fault_type"),
    )
    with pytest.raises(ConfigurationError, match="unknown fault type 'unknown_fault_type'"):
        FaultScheduler(faults)


def test_fault_scheduler_invalid_repeat_rejection() -> None:
    faults = (
        FaultSpec(id="f1", at="before_commit", type="approval_expired", repeat=0),
    )
    with pytest.raises(ConfigurationError, match="repeat count must be at least 1"):
        FaultScheduler(faults)


def test_fault_scheduler_one_shot_and_repeat_limits() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "rec-1", "status": "active"}]})

    # One-shot fault (default repeat=1)
    f_one_shot = FaultSpec(id="f-stale", at="after_read", type="stale_read", target="rec-1")
    scheduler = FaultScheduler([f_one_shot])

    obs = {"id": "rec-1", "status": "active", "version": "v2"}

    # First trigger: applied
    res1 = scheduler.after_read("rec-1", obs, world, clock)
    assert res1.applied is True
    assert res1.modified_observation is not None
    assert res1.modified_observation["version"] == "v1"

    # Second trigger: skipped because repeat limit (1) reached
    res2 = scheduler.after_read("rec-1", obs, world, clock)
    assert res2.applied is False

    assert len(scheduler.get_events()) == 1


def test_fault_scheduler_repeat_multiple_times() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "rec-1", "status": "active"}]})

    f_repeat = FaultSpec(
        id="f-stale-repeat",
        at="after_read",
        type="stale_read",
        target="rec-1",
        repeat=3,
    )
    scheduler = FaultScheduler([f_repeat])
    obs = {"id": "rec-1", "status": "active", "version": "v2"}

    for i in range(3):
        res = scheduler.after_read("rec-1", obs, world, clock)
        assert res.applied is True

    # 4th trigger: exhausted
    res4 = scheduler.after_read("rec-1", obs, world, clock)
    assert res4.applied is False
    assert len(scheduler.get_events()) == 3


def test_fault_scheduler_reset_and_replay() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "rec-1", "status": "active"}]})
    f = FaultSpec(id="f1", at="after_read", type="stale_read", target="rec-1")
    scheduler = FaultScheduler([f])

    obs = {"id": "rec-1", "status": "active", "version": "v2"}
    res1 = scheduler.after_read("rec-1", obs, world, clock)
    assert res1.applied is True
    assert len(scheduler.get_events()) == 1

    # Reset
    scheduler.reset()
    assert len(scheduler.get_events()) == 0

    # Replay
    res2 = scheduler.after_read("rec-1", obs, world, clock)
    assert res2.applied is True
    assert len(scheduler.get_events()) == 1
    assert scheduler.get_events()[0].event_id == "evt_fault_f1_1"


def test_fault_event_serialization() -> None:
    evt = FaultEvent(
        event_id="evt_test_1",
        fault_id="f1",
        fault_type="stale_read",
        lifecycle_point="after_read",
        virtual_timestamp="2026-01-01T09:00:00Z",
        target_entity_id="rec-1",
        trigger_count=1,
        status="applied",
        reason="injected stale read",
    )
    d = evt.to_dict()
    assert d["event_id"] == "evt_test_1"
    assert d["fault_type"] == "stale_read"
    assert d["virtual_timestamp"] == "2026-01-01T09:00:00Z"
