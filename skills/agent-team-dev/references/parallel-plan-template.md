---
tags: [tools, codex-agent-team-template, skills, agent-team-dev, parallel, plan, template, ai, build, worktree]
timestamp: 2026-08-01T00:00:00.000Z
---
# Parallel Plan Template

Use this template when writing an implementation plan that may be executed by
multiple implementers concurrently. Every writable task runs worktree-per-task
(see SKILL.md "Isolation model"), including serial tasks.

## Required task fields

Each task must include:

```text
Task ID:
Title:
Agent:
Parallel group:
Depends on:
Branch:            agent/<run-id>/<task-id>
Worktree:          .agents/runs/<run-id>/worktrees/<task-id>
Write set:
Read set:
Forbidden paths:
Allowed shared surfaces: (normally empty; forces serial execution)
Conflicts with:
Resource namespaces:   (TMPDIR / port range / test DB / compose project / build dir)
Contract:
Tests:
Verification:
Stop rule:
Done gate:
```

## Parallel safety rules

- Only tasks in the same `parallel_group` may run concurrently.
- A task may start only after every `depends_on` task is merged.
- Every parallel task runs in its own worktree on its own task branch, forked
  from the integration branch head; write sets must still not overlap.
- Tests belong to the feature task: a task's `write_set` includes its own test
  paths, and no second writer touches them concurrently. Test design (read-
  only) runs before the wave; gap-fill and integration tests run serially
  after merge.
- Non-overlapping write sets are not isolated execution: tasks that touch
  runtime resources need namespaced TMPDIR, ports, test DB names, compose
  project names, and worktree-local build dirs. Two tasks never share a
  mutable runtime resource in the same wave.
- Developers run only task-scoped verification in their worktree; repo-wide
  builds, full suites, shared-DB and real-service tests run serially on the
  integration branch after merge.
- Public interfaces, schemas, migrations, generated files, package/build/
  config files, global fixtures, snapshots, and shared test infrastructure are
  serial by default.
- Any implementer that needs to edit outside its `write_set` must stop and
  report the reason to the coordinator (task goes to `blocked`).
- A task merges only after `.codex/bin/agent-team-check-task` passes; the
  coordinator then cherry-picks its commits into the integration branch in
  dependency order and resolves conflicts itself.
- Review starts after the whole wave merges, on the integration diff, not
  after each individual parallel task.

## Example

```markdown
## P0: Contracts / Serial setup

- [ ] P0-A Define shared interface
  - Agent: architect (design) + developer (serial implementation)
  - Parallel group: serial
  - Depends on: none
  - Branch: agent/20260801-auth/P0-A
  - Worktree: .agents/runs/20260801-auth/worktrees/P0-A
  - Write set:
    - src/shared/types.ts
    - docs/plans/<plan>.md
  - Read set:
    - src/**
    - tests/**
  - Forbidden paths:
    - migrations/**
  - Conflicts with: all tasks that consume this interface until merged
  - Contract:
    - exported types and function signatures are stable for Wave 1
  - Tests:
    - typecheck
  - Verification:
    - npm run typecheck
  - Stop rule:
    - stop if public API impact is larger than planned
  - Done gate:
    - check-task passes; coordinator approves contract before Wave 1

## Wave 1: Independent implementation

- [ ] P1-A Implement auth middleware
  - Agent: developer
  - Parallel group: wave-1
  - Depends on: P0-A
  - Branch: agent/20260801-auth/P1-A
  - Worktree: .agents/runs/20260801-auth/worktrees/P1-A
  - Write set:
    - src/auth/**
    - tests/auth/**
  - Read set:
    - src/shared/types.ts
    - src/server.ts
  - Forbidden paths:
    - package.json
    - migrations/**
  - Conflicts with:
    - any task editing src/auth/**
  - Resource namespaces:
    - TMPDIR=.agents/tmp/P1-A, PORT_BASE=42100, TEST_DB_NAME=test_auth_p1a
  - Contract:
    - expose requireAuth(req, res, next)
    - no DB schema changes
  - Tests:
    - focused auth middleware tests
  - Verification:
    - npm test -- auth
  - Stop rule:
    - stop before editing src/server.ts or shared types
  - Done gate:
    - focused tests pass in the task worktree; check-task passes

- [ ] P1-B Implement billing adapter
  - Agent: developer
  - Parallel group: wave-1
  - Depends on: P0-A
  - Branch: agent/20260801-auth/P1-B
  - Worktree: .agents/runs/20260801-auth/worktrees/P1-B
  - Write set:
    - src/billing/**
    - tests/billing/**
  - Read set:
    - src/shared/types.ts
  - Forbidden paths:
    - package.json
    - migrations/**
  - Conflicts with:
    - any task editing src/billing/**
  - Resource namespaces:
    - TMPDIR=.agents/tmp/P1-B, PORT_BASE=42200, TEST_DB_NAME=test_billing_p1b
  - Contract:
    - expose BillingAdapter interface implementation
  - Tests:
    - focused billing adapter tests
  - Verification:
    - npm test -- billing
  - Stop rule:
    - stop before editing auth/server wiring
  - Done gate:
    - focused tests pass in the task worktree; check-task passes

## Wave 1 merge gate

- [ ] M1 Merge and verify Wave 1
  - Agent: coordinator
  - Parallel group: serial
  - Depends on: P1-A, P1-B
  - Actions:
    - run agent-team-check-task for P1-A and P1-B (must pass)
    - cherry-pick task commits into agent/20260801-auth/integration in
      dependency order; resolve conflicts serially
    - run the wave's focused tests together on the integration branch
    - run test-writer worker (serial, isolated) if focused-test gaps remain
    - run integration-tester worker (serial, isolated) across real boundaries
    - create immutable review packets; spawn spec_reviewer and standards_reviewer
    - spawn risk_reviewer only when a high-risk trigger applies
    - fix every confirmed finding via fix tasks; re-review until findings close
    - update lifecycle through agent-team-state before Wave 2
```
