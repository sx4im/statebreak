"""Deterministic in-process multi-node coordination and message passing harness."""

from __future__ import annotations

import copy
from typing import Any

from statebreak.adapter import CoordinationMessage
from statebreak.canonical import canonical_json, compute_sha256
from statebreak.clock import VirtualClock
from statebreak.errors import UsageError

MAX_MESSAGE_PAYLOAD_SIZE = 65_536  # 64 KB
MAX_QUEUE_CAPACITY = 1_000


class MessageQueue:
    """Deterministic, synchronous in-process message queue for multi-node simulation."""

    def __init__(
        self,
        nodes: tuple[str, ...] | list[str] = ("node-01", "node-02"),
        run_id: str = "run_default",
    ) -> None:
        self._run_id = run_id
        self._nodes = set(nodes)
        self._mailboxes: dict[str, list[CoordinationMessage]] = {nid: [] for nid in self._nodes}
        self._history: list[CoordinationMessage] = []
        self._seq_counter: int = 0

    @property
    def nodes(self) -> tuple[str, ...]:
        """Return sorted registered node IDs."""
        return tuple(sorted(self._nodes))

    def register_node(self, node_id: str) -> None:
        """Register a new node in the coordination harness."""
        if not node_id or not isinstance(node_id, str):
            raise UsageError("node ID must be a non-empty string")
        if node_id not in self._nodes:
            self._nodes.add(node_id)
            self._mailboxes[node_id] = []

    def _next_message_id(self) -> str:
        self._seq_counter += 1
        return f"msg_{self._run_id}_{self._seq_counter:04d}"

    def send(
        self,
        sender_id: str,
        recipient_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        operation_id: str | None = None,
        entity_id: str | None = None,
        expected_version: str | None = None,
        clock: VirtualClock | None = None,
    ) -> CoordinationMessage:
        """Enqueue a typed coordination message to recipient node or broadcast with '*'."""
        if sender_id not in self._nodes:
            raise UsageError(
                f"unknown sender node '{sender_id}' (registered: {sorted(self._nodes)})"
            )

        payload_dict = copy.deepcopy(payload) if payload is not None else {}
        payload_json = canonical_json(payload_dict)
        if len(payload_json) > MAX_MESSAGE_PAYLOAD_SIZE:
            raise UsageError(
                f"coordination message payload exceeds maximum size of "
                f"{MAX_MESSAGE_PAYLOAD_SIZE} bytes"
            )

        payload_hash = compute_sha256(payload_json)
        ts = clock.now_iso() if clock is not None else ""

        recipients = (
            [nid for nid in sorted(self._nodes) if nid != sender_id]
            if recipient_id == "*"
            else [recipient_id]
        )

        last_msg: CoordinationMessage | None = None
        for target_node in recipients:
            if target_node not in self._nodes:
                raise UsageError(
                    f"unknown recipient node '{target_node}' (registered: {sorted(self._nodes)})"
                )

            if len(self._mailboxes[target_node]) >= MAX_QUEUE_CAPACITY:
                raise UsageError(
                    f"node '{target_node}' mailbox capacity exceeded ({MAX_QUEUE_CAPACITY})"
                )

            msg_id = self._next_message_id()
            msg = CoordinationMessage(
                message_id=msg_id,
                run_id=self._run_id,
                sender_id=sender_id,
                recipient_id=target_node,
                message_type=message_type,
                operation_id=operation_id,
                entity_id=entity_id,
                expected_version=expected_version,
                payload_hash=payload_hash,
                virtual_timestamp=ts,
                payload=payload_dict,
            )
            self._mailboxes[target_node].append(msg)
            self._history.append(msg)
            last_msg = msg

        if last_msg is None:
            raise UsageError(
                f"no valid recipients for message from '{sender_id}' to '{recipient_id}'"
            )

        return last_msg

    def receive(self, node_id: str) -> CoordinationMessage | None:
        """Pop and return the oldest pending message for node_id."""
        if node_id not in self._nodes:
            raise UsageError(f"unknown node '{node_id}'")
        if not self._mailboxes[node_id]:
            return None
        return self._mailboxes[node_id].pop(0)

    def peek(self, node_id: str) -> CoordinationMessage | None:
        """Inspect the oldest pending message without popping it."""
        if node_id not in self._nodes:
            raise UsageError(f"unknown node '{node_id}'")
        if not self._mailboxes[node_id]:
            return None
        return self._mailboxes[node_id][0]

    def receive_all(self, node_id: str) -> tuple[CoordinationMessage, ...]:
        """Pop and return all pending messages for node_id."""
        if node_id not in self._nodes:
            raise UsageError(f"unknown node '{node_id}'")
        msgs = list(self._mailboxes[node_id])
        self._mailboxes[node_id].clear()
        return tuple(msgs)

    def has_messages(self, node_id: str) -> bool:
        """Check if node_id has any pending messages."""
        return node_id in self._nodes and len(self._mailboxes[node_id]) > 0

    def pending_count(self, node_id: str) -> int:
        """Return number of pending messages for node_id."""
        return len(self._mailboxes.get(node_id, []))

    def duplicate_message(self, message_id: str) -> bool:
        """Duplicate an existing message in its recipient queue to simulate network duplicate."""
        for nid, box in self._mailboxes.items():
            for i, msg in enumerate(box):
                if msg.message_id == message_id:
                    dup = copy.deepcopy(msg)
                    box.insert(i + 1, dup)
                    return True
        return False

    def drop_message(self, message_id: str) -> bool:
        """Simulate packet loss by removing a message from the recipient queue before delivery."""
        for nid, box in self._mailboxes.items():
            for i, msg in enumerate(box):
                if msg.message_id == message_id:
                    box.pop(i)
                    return True
        return False

    def reorder_messages(self, node_id: str, new_indices: list[int]) -> None:
        """Deterministically reorder pending messages in a node's mailbox."""
        if node_id not in self._nodes:
            raise UsageError(f"unknown node '{node_id}'")
        box = self._mailboxes[node_id]
        if len(new_indices) != len(box) or set(new_indices) != set(range(len(box))):
            raise UsageError(
                f"invalid reorder permutation for {len(box)} messages: {new_indices}"
            )
        self._mailboxes[node_id] = [box[i] for i in new_indices]

    def get_history(self) -> tuple[CoordinationMessage, ...]:
        """Return complete chronological history of all sent messages."""
        return tuple(self._history)

    def reset(self) -> None:
        """Reset all mailboxes, message history, and sequence counters."""
        for nid in self._mailboxes:
            self._mailboxes[nid].clear()
        self._history.clear()
        self._seq_counter = 0
