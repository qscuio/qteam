---
name: verification-before-completion
description: Require fresh command evidence before any QTeam completion claim.
---

# Verification Before Completion

Identify the command that proves the claimed behavior at the approved seam,
run it fresh, inspect its complete exit status/output, and preserve the evidence.
Task evidence is recorded only through `agent-team-state verify-task`; final
evidence only through `verify-final`, both bound to Git HEAD.

Do not infer success from earlier output, a worker narrative, partial tests, or
the absence of visible errors. Return control to QTeam; this skill does not own
state transitions or finish.
