# Apex AI Phase 74 — Tool Permissions

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `f3d9143`, immediately following Phase 73 (Tool Architecture)
- **Scope:** "Require explicit permission boundaries for tools and prevent
  unrestricted actions." Builds directly on Phase 73's `Tool`/`ToolRegistry`
  without changing anything about how a resolved tool call actually
  executes — this phase decides *whether* a call is allowed to reach
  `ToolRegistry.execute()` at all, and bounds how many times it can happen
  in one turn.

## Design: two independent guarantees

1. **Per-tool consent.** `Tool` (Phase 73) gained a `requires_permission:
   bool = True` field — conservative by default, so a new tool is gated
   unless its author explicitly marks it side-effect-free. A tool the
   caller has not listed in `granted_tools` for this request cannot run,
   full stop.
2. **A hard per-turn cap.** Even a fully granted tool cannot be called an
   unbounded number of times in one model turn (`MAX_TOOL_CALLS_PER_TURN =
   8`, a fixed safety ceiling, not a tuning knob — same precedent as Phase
   73's `MAX_RESULT_CHARS`). A misbehaving or manipulated model looping on
   tool calls hits a bounded, clearly-worded refusal instead of running
   indefinitely.

`apex_ai/tools/permissions.py`'s `PermissionedToolExecutor` wraps a
`ToolRegistry` with both checks and is the entry point a real caller uses
instead of `ToolRegistry.execute()` directly. It also exposes `schema()`,
a permission-aware variant of Phase 73's `ToolRegistry.schema()`: a gated
tool that hasn't been granted is not even included in what gets offered to
the model this turn (least-privilege in the schema itself, not only at
execution time — the model is never told about a tool it cannot use).

The unknown-tool-name and malformed-argument handling `ToolRegistry.execute()`
already provides (Phase 73) is untouched: a permission check only sits in
front of it, and an unrecognized tool name still gets the exact same
"No tool named …" bounded error regardless of what was granted.

## Files

- `apex_ai/tools/base.py` — `Tool.requires_permission: bool = True`
- `apex_ai/tools/permissions.py` (new) — `ToolCallBudget`,
  `PermissionedToolExecutor`
- `apex_ai/tools/__init__.py` — re-exports the above
- `tests/test_tool_permissions.py` (new)

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 394 passed, 3 skipped |
| `tests/test_tool_permissions.py` | 10 passed |
| `ruff check` on every touched/new file | clean |

## Deliberately not done in this phase

- **No live wiring into `runtime.py`/chat.** Same reasoning as Phase 73:
  there is still no real registered tool to gate (Phase 76 adds the first
  one, calculator/data), so there is nothing yet for a live
  `PermissionedToolExecutor` to protect.
- **No per-account/persisted grant store.** `granted_tools` is passed in by
  the caller per call, deliberately not backed by a database yet — what
  "granting" a tool should mean for a real account (a one-time UI consent
  prompt? a per-project setting alongside Phase 71's project instructions?)
  is a product decision for whichever phase actually exposes a tool a user
  would need to grant, not something to guess at in the abstraction layer.
- **No configurable `APEX_MAX_TOOL_CALLS_PER_TURN` setting.** Kept as a
  fixed constant, consistent with Phase 73's `MAX_RESULT_CHARS` decision;
  `PermissionedToolExecutor.__init__` already accepts an explicit override,
  so a future phase can promote it to a `Settings` field once something
  real constructs this class from `runtime.py` and needs that knob exposed.
