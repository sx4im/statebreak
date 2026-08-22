# Glossary

**Authoritative state:** The state maintained by the fixture world and inspected by the oracle; it outranks agent claims and stale observations.

**Claim:** A structured assertion emitted by an adapter, such as `refund_committed`.

**Committed effect:** An effect the fake provider records as applied to the authoritative world.

**False success:** A completion claim that conflicts with the authoritative final state or required postconditions.

**Fault:** A deterministic state change or ambiguous outcome injected at a named lifecycle point.

**Handoff:** The structured state passed from one agent run or worker to another.

**Oracle:** A deterministic evaluator that checks invariants and effects against authoritative state.

**State conflict:** A mismatch between the state an agent observed or assumed and the state at action or verification time.

**Unknown outcome:** An effect whose external commitment cannot yet be established, such as a timeout after commit.
