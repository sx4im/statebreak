# Architecture

StateBreak is a deterministic testing harness for AI agents operating on stateful systems. It tests how agent integrations behave under state drift, network ambiguity, clock jumps, expired approvals, and duplicate retries.

```
┌─────────────────────────────────────────────────────────────┐
│                       ScenarioRunner                        │
│                                                             │
│   ┌──────────────┐      ┌───────────────┐      ┌────────┐   │
│   │ VirtualClock │◄────►│  LocalWorld   │◄────►│ Oracle │   │
│   └──────┬───────┘      └───────┬───────┘      └────▲───┘   │
│          │                      │                   │       │
│          ▼                      ▼                   │       │
│   ┌──────────────┐      ┌───────────────┐           │       │
│   │FaultScheduler│◄────►│  ToolGateway  │           │       │
│   └──────────────┘      └───────┬───────┘           │       │
│                                 │                   │       │
│                                 ▼                   │       │
│                         ┌───────────────┐           │       │
│                         │ AgentAdapter  │───────────┘       │
│                         └───────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Virtual Clock (`src/statebreak/clock.py`)
All timestamps in StateBreak are derived from an in-memory virtual clock initialized from scenario configuration. Time only advances through explicit increments. Wall-clock functions (`time.time()`, `datetime.now()`) are prohibited in execution paths to guarantee deterministic replay across machines.

### 2. Authoritative Local World (`src/statebreak/world.py`)
The local world holds the ground-truth state of all entities (e.g., approvals, orders, accounts). It maintains:
- Sequential versioning (`v1` -> `v2`) on all mutations.
- Version-locked updates (`expected_version`).
- An immutable side-effect ledger recording all committed operations.
- Deep-copied read observations to prevent in-memory mutation leaks.

### 3. Fault Scheduler (`src/statebreak/faults.py`)
The fault scheduler injects declared failure modes at specific points in the tool lifecycle:
- `before_read`: Intercepts state queries before the world processes them.
- `after_read`: Mutates or degrades observations returned to the agent (e.g. stale state).
- `before_commit`: Modifies commit targets or expires approvals before writes apply.
- `after_commit_before_response`: Alters return outcomes after writes commit (e.g. simulating timeout ambiguity).
- `before_retry`: Intercepts and tracks unverified retry attempts.
- `handoff_emit`: Drops or truncates constraints when tasks transfer between agents.

### 4. Tool Gateway (`src/statebreak/gateway.py`)
The tool gateway is the sole interface exposed to agent adapters. It enforces tool allowlists, dispatches fault hooks, and returns structured `ToolOutcome` objects. When a commit timeout occurs, the gateway returns `status="unknown"` while the authoritative world retains the committed effect, forcing the agent to handle ambiguity realistically.

### 5. Multi-Node Coordination (`src/statebreak/coordination.py`, `src/statebreak/convergence.py`)
Multi-agent scenarios use a synchronous in-process message queue with bounded mailboxes. Convergence trackers monitor node-local version views against authoritative state, allowing tests to verify whether multi-agent systems detect split-brain conditions and reconcile state safely.

### 6. Authoritative Oracle Engine (`src/statebreak/oracle.py`)
The oracle evaluates the execution evidence after the agent finishes. It compares the authoritative world state and effect ledger against scenario invariants. Agent claims (such as reporting `task_completed=True`) are treated as unverified unless corroborated by ground-truth state.
