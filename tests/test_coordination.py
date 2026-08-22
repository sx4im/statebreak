"""Unit tests for in-process MessageQueue and multi-node coordination."""

from __future__ import annotations

import pytest

from statebreak.clock import VirtualClock
from statebreak.coordination import MessageQueue
from statebreak.errors import UsageError


def test_coordination_node_registration_and_send_receive() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-test")

    assert queue.nodes == ("node-01", "node-02")
    assert not queue.has_messages("node-02")

    # Send message from node-01 to node-02
    msg = queue.send(
        sender_id="node-01",
        recipient_id="node-02",
        message_type="state_update",
        payload={"key": "val"},
        operation_id="op_1",
        entity_id="e1",
        clock=clock,
    )
    assert msg.message_id == "msg_run-test_0001"
    assert msg.sender_id == "node-01"
    assert msg.recipient_id == "node-02"
    assert queue.has_messages("node-02")
    assert queue.pending_count("node-02") == 1

    # Peek without popping
    peeked = queue.peek("node-02")
    assert peeked is not None
    assert peeked.message_id == "msg_run-test_0001"
    assert queue.pending_count("node-02") == 1

    # Receive
    rec = queue.receive("node-02")
    assert rec is not None
    assert rec.message_id == "msg_run-test_0001"
    assert not queue.has_messages("node-02")


def test_coordination_broadcast() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02", "node-03"], run_id="run-bcast")

    # Broadcast from node-01 to all other nodes
    queue.send(
        sender_id="node-01",
        recipient_id="*",
        message_type="sync_announcement",
        payload={"state": "v2"},
    )
    assert queue.pending_count("node-01") == 0
    assert queue.pending_count("node-02") == 1
    assert queue.pending_count("node-03") == 1


def test_coordination_duplicate_message_delivery() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-dup")

    msg = queue.send("node-01", "node-02", "task", {"id": 1})
    assert queue.pending_count("node-02") == 1

    # Inject duplicate
    dup_ok = queue.duplicate_message(msg.message_id)
    assert dup_ok is True
    assert queue.pending_count("node-02") == 2

    m1 = queue.receive("node-02")
    m2 = queue.receive("node-02")
    assert m1 is not None and m2 is not None
    assert m1.message_id == m2.message_id
    assert m1.payload == m2.payload


def test_coordination_drop_message_delivery() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-drop")

    msg = queue.send("node-01", "node-02", "task", {"id": 1})
    assert queue.pending_count("node-02") == 1

    # Drop message
    drop_ok = queue.drop_message(msg.message_id)
    assert drop_ok is True
    assert queue.pending_count("node-02") == 0
    assert queue.receive("node-02") is None


def test_coordination_deterministic_reordering() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-reorder")

    queue.send("node-01", "node-02", "msg", {"seq": 1})
    queue.send("node-01", "node-02", "msg", {"seq": 2})
    queue.send("node-01", "node-02", "msg", {"seq": 3})

    assert queue.pending_count("node-02") == 3

    # Reorder [0, 1, 2] -> [2, 0, 1]
    queue.reorder_messages("node-02", [2, 0, 1])

    m1 = queue.receive("node-02")
    m2 = queue.receive("node-02")
    m3 = queue.receive("node-02")

    assert m1 is not None and m1.payload["seq"] == 3
    assert m2 is not None and m2.payload["seq"] == 1
    assert m3 is not None and m3.payload["seq"] == 2


def test_coordination_unknown_node_rejections() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02"])

    with pytest.raises(UsageError, match="unknown sender"):
        queue.send("unknown-sender", "node-02", "msg")

    with pytest.raises(UsageError, match="unknown recipient"):
        queue.send("node-01", "unknown-recipient", "msg")

    with pytest.raises(UsageError, match="unknown node"):
        queue.receive("unknown-node")


def test_coordination_reset_and_history() -> None:
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-hist")
    queue.send("node-01", "node-02", "m1")
    queue.send("node-02", "node-01", "m2")

    assert len(queue.get_history()) == 2

    # Reset
    queue.reset()
    assert len(queue.get_history()) == 0
    assert queue.pending_count("node-01") == 0
    assert queue.pending_count("node-02") == 0
