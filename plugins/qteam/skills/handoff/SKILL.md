---
name: handoff
description: Compact the current Codex, Claude Code, or Cursor conversation into a secret-redacted handoff document so a fresh agent or human can continue without replaying the transcript. Use only when the user explicitly asks to hand off, switch harness/session/owner, or prepare a continuation packet.
---

# Handoff

Create a compact pointer-rich handoff for a fresh session or owner. Save it in
the operating system's temporary directory, not the repository, unless the user
explicitly requests a durable project artifact.

This skill adapts Matt Pocock's handoff primitive to QTeam. It does not create a
second run state. When a QTeam run exists, its durable state, Git commits,
review ledgers, and proof artifacts remain authoritative; the handoff only tells
the next agent where to resume.

## Procedure

1. Read the user's optional argument as the next session's focus.
2. Detect whether the current work belongs to an active QTeam run. If it does,
   obtain a fresh compact packet with:

   ```bash
   .codex/bin/agent-team-goal --run <run-id> status
   ```

   Record the run ID, checkpoint, phase, next action, blocking decisions,
   blocking typed handoffs, and dependency status. Never infer completion from
   the transcript.
3. Inspect the current Git branch, HEAD, and bounded `git status --short`. Do
   not stage, commit, switch branches, or mutate run state.
4. Link existing specs, plans, ADRs, issues, commits, diffs, logs, and review
   receipts by path or URL. Do not copy their contents into the handoff.
5. Write the packet using
   [`references/handoff-template.md`](references/handoff-template.md). Omit
   empty optional sections.
6. Redact secrets, credentials, tokens, cookies, private keys, personal data,
   and sensitive tool output. Prefer a safe location description such as
   “credential is in the configured secret store”; never paste the value.
7. Save to a fresh, mode-private temporary file. On POSIX, use `mktemp` and
   `chmod 600`; on Windows, create a unique file beneath the OS temp directory.
8. Return the exact file path and one sentence describing the intended next
   action.

## Content rules

- State current facts, not a chronological transcript summary.
- Separate **done**, **in progress**, and **not started**.
- Preserve unresolved questions and the evidence needed to answer them.
- Include exact verification commands and their last known outcomes; never say
  “tests pass” without naming the evidence.
- Include user constraints that materially affect the next turn.
- Suggested skills must be installed, relevant, and named exactly. For a QTeam
  delivery resume, normally suggest `$qteam-router`, `$qteam-goal`, and the one
  method skill required by the next action—not the whole catalog.
- Keep it below 1,500 words. If more context is needed, link the artifact.

## QTeam boundary

The conversation handoff is different from a task's durable typed handoff:

- this skill changes session/owner context;
- `agent-team-state` typed handoffs express successor, user-decision, replan,
  or no-followup requirements inside a QTeam run.

Do not close, delete, or rewrite a durable typed handoff while creating this
document. If the next session must resolve one, name it as a blocker and point
to the run status packet.

## Completion check

- [ ] The next-session focus and immediate next action are explicit.
- [ ] QTeam checkpoint/run ID is present when applicable.
- [ ] Existing artifacts are linked rather than duplicated.
- [ ] Git identity and working-tree state are bounded and current.
- [ ] Verification evidence and blockers are honest.
- [ ] Suggested skills exist and are minimal.
- [ ] Secrets and personal data are absent.
- [ ] The file is outside the repository and mode-private by default.
