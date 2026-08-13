---
name: qteam-harden
description: Define and execute QTeam's policy-triggered refactor, hardening, and public-surface QA lanes. Use when task-put derives standard/hardened workflow shape or required_quality_lanes, when a project policy raises the workflow floor, or when compatibility/public API/security/concurrency/migration/data-loss work needs head-bound quality evidence before review.
---

# QTeam Harden

This skill is a bounded quality primitive inside `agent-team-dev`; it is not an
orchestrator or an extra reviewer. Use only the lanes named by the task's
derived policy. Mandatory spec and standards review still run afterward.

## Freeze lane commands during planning

For every lane in `policy.required_quality_lanes`, put one or more deterministic
commands in the task's `quality_commands.<lane>` array. Commands are frozen by
`task-put`, aggregated once per wave, deduplicated, and replayed on the exact
integration HEAD. They must prove the lane's acceptance property rather than
merely printing a claim.

Read [quality-lanes.md](references/quality-lanes.md) to choose evidence:

- `refactor`: behavior-preserving simplification after GREEN, checked by the
  focused suite plus an applicable structural/static rule. Do not generalize
  unrelated code.
- `hardening`: use mutation, property/generative, fault-injection, race, or
  rollback checks appropriate to the named risk. A second ordinary test run is
  not hardening evidence.
- `public-surface-qa`: exercise the external consumer surface, compatibility
  path, examples/help/docs, and negative input behavior that users actually see.

If no repository tool can express a required property, create a normal
write-scoped QTeam task that adds the smallest durable test/check first. Never
replace the lane with an unverified prose assertion.

## Execute after integration

After the wave is merged and integration tests are green, run each required
lane:

```bash
.codex/bin/agent-team-state --run <run> quality-assess \
  --wave <N> --lane refactor --outcome not-needed \
  --rationale "bounded post-GREEN ownership/duplication assessment"
.codex/bin/agent-team-state --run <run> quality-check \
  --wave <N> --lane <refactor|hardening|public-surface-qa>
```

For `refactor`, the assessment is mandatory and head-bound. If simplification
is needed, use `--outcome task-created --task <same-wave-integrated-refactor>`;
the cited task must already be mechanically checked and merged. Other lanes do
not add prose assessments.

The state manager runs only the frozen commands in a disposable detached
checkout of the exact integration HEAD, bounds retained output, records both
streams and the HEAD, and fails the
transition to review when a lane is absent, failed, or stale.

Frozen commands must be reproducible from tracked sources and repository-native
bootstrap/cache mechanisms. They cannot depend on ignored files that exist only
in the authoritative integration checkout.

When a lane fails, create an owned isolated task/fix, gate and merge it, then
rerun every required lane on the new integration HEAD. Do not weaken the
command. Later-wave changes make earlier evidence stale; rerun the triggered
lanes once on the final HEAD before finish.

## Cost boundary

Quality lanes add deterministic evidence, not a new LLM review per task. Share
identical commands across a wave, keep one strongest command per property, and
do not trigger lanes that the frozen policy did not require. Review remains
wave-level and finding-owned re-review remains diff-scoped.
