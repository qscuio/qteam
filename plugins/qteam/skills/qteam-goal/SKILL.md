---
name: qteam-goal
description: Keep an active QTeam run advancing inside a Codex, Claude Code, or Cursor session while treating durable run state—not transcript memory—as the goal authority.
---

# QTeam Goal

Use this for an explicit long-running or autonomous QTeam goal. It is a thin
host adapter over `agent-team-dev`; it does not create another orchestrator or
another copy of delivery state.

## Durable authority

The QTeam run or epic is the durable goal. Its state, Git heads, task records,
review ledgers, and proof artifacts decide completion. A host-native goal only
keeps the current session taking turns; it is never completion evidence.

Start with:

```bash
.codex/bin/agent-team-goal --run <run-id> status
.codex/bin/agent-team-goal --run <run-id> condition --host <codex|claude|cursor>
```

The condition terminates host continuation when the run reaches durable
`DONE`. A human decision pauses the host loop without completing the QTeam
goal. Resume after the decision from a new `status` packet.

## Host adapters

- **Codex:** when the host exposes a native goal tool and the user requested
  autonomous continuation, create or update it with the exact generated
  condition. The native goal status is a session lease, not QTeam state.
- **Claude Code:** use the generated `/goal ...` command. Claude's evaluator
  must see a fresh QTeam goal-status result. The `/goal` lease is satisfied by
  durable `achieved` or `waiting-for-human`; the latter pauses for user input
  without completing QTeam and requires a new generated lease after the answer.
- **Cursor:** use the generated `cursor-stop` command as a project stop hook.
  It emits `followup_message` only while the durable run is actionable and
  stops on completion, a human decision, an aborted/error turn, or its explicit
  iteration safety bound.

Do not silently substitute a host adapter that is unavailable. The run remains
resumable, but unattended continuation is blocked until the named native
facility or hook is configured.

## Waiting without model polling

When the status packet says `waiting-for-external-work`, call one blocking wait:

```bash
.codex/bin/agent-team-goal --run <run-id> wait \
  --after <checkpoint> --timeout 300
```

The command watches the bounded durable run projection inside one tool call.
It returns immediately when state, worker results, or review receipts change.
Do not spend repeated model turns calling `status`. Native push notifications
may wake the host sooner, but the durable checkpoint makes a missed notification
recoverable.

## Session boundary

Continuation is the default while the next coordinator phase still benefits
from the current primary conversation. Decide only at a phase boundary:

1. Continue when the next phase needs the current reasoning and the session is
   still coherent.
2. Clear when the old context is irrelevant.
3. Handoff only when changing harness, repository/directory, or human owner.
4. Use a fresh subagent for a bounded AFK task.
5. Compact only when relevant context must stay in the same host session but
   no longer fits cleanly.

QTeam's durable status makes a fresh main session safe, but never makes it
mandatory. After compaction or a new session, trust `status`, proof artifacts,
and Git over remembered conversation summaries.
