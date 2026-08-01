# QTeam

QTeam is a local, durable software-development orchestrator for Codex. It
combines the strongest planning and verification ideas from Superpowers and
Matt Pocock's skills while keeping exactly one orchestration authority.

The core rule is simple: roles are created only for distinct permission,
context, output, or lifecycle boundaries; reusable practices remain skills.

## What changed in 0.4

- Writable work no longer uses native subagents. `agent-team-worker` launches
  a separate `codex exec` pinned to each task's Git worktree and records its
  PID, cwd, logs, and durable result.
- `agent-team-state` is the only supported state mutator. It validates phase
  and task transitions with locked, fsynced atomic writes and an event log.
- The old dual-mode tester is split into read-only `test_designer` and isolated
  writable `test-writer`; integration testing is also an isolated serial worker.
- Reviews always require independent `spec` and `standards` (code-quality)
  axes; `risk` is an additional triggered axis. Packets carry
  fixed base/head SHAs, three-dot ranges, commit lists, content-addressed
  source snapshots, a JSON finding ledger, and an independent result
  attestation.
- `qteam-router`, `qteam-tdd`, `qteam-diagnose`, and `qteam-review` unify the
  overlapping Superpowers/Matt flows. Grilling, domain modeling, spec synthesis,
  ticket slicing, and wayfinding are bounded design primitives.
- `goal-execution-discipline` is the standing no-workaround, full-scope,
  evidence, honest-status, and mandatory dual-review contract.

`skills/agent-team-dev/SKILL.md` is the normative workflow. This README is an
operator guide.

## Install

From this repository:

```bash
./install.sh /path/to/target-git-repository
cd /path/to/target-git-repository
.codex/bin/agent-team-doctor
```

The installer backs up changed managed files, removes obsolete writable native
agent definitions, and installs:

```text
.codex/agents/             read-only roles only
.codex/worker-prompts/     writable role contracts
.codex/bin/                wake, state, worker, gate, review, finish, doctor
.codex/schemas/            run/task/worker/verification/finding/review schemas
.agents/skills/            QTeam + bounded Superpowers/Matt-derived primitives
```

Use `agent-team-doctor --smoke` to spend one small Codex call verifying that a
named read-only role loads with `fork_turns=none`.

## Start or resume

Interactive:

```bash
.codex/bin/wake-agent-team "implement the approved goal"
```

Unattended execution requires an active run, an approved plan, or explicit
permission to record assumptions:

```bash
.codex/bin/wake-agent-team --exec --plan docs/plans/auth.md "execute this plan"
.codex/bin/wake-agent-team --exec --allow-assumptions "small bounded change"
```

Create and advance durable state through the command, never by editing JSON:

```bash
.codex/bin/agent-team-state --run 20260801-auth init --goal "add auth"
.codex/bin/agent-team-state --run 20260801-auth phase SPEC_READY
.codex/bin/agent-team-state --run 20260801-auth task-put --file /tmp/T01.json
.codex/bin/agent-team-state --run 20260801-auth verify-task T01
.codex/bin/agent-team-state --run 20260801-auth verify-final \
  --command "pytest -q"
```

Register repository-specific shared surfaces at `init` with repeatable
`--shared-surface <glob>`; any task allowed to touch one must be explicitly
declared serial, and the runtime gate checks the actual changed paths.

## Execution model

```text
Coordinator (main session; orchestration/integration only)
├── read-only native agents, always fork_turns=none
│   ├── researcher / architect / parallel_planner / test_designer
│   └── spec_reviewer / standards_reviewer / risk_reviewer (on trigger)
├── integration branch + per-task branch/worktree
├── isolated writable processes
│   ├── developer / debugger variants
│   ├── test-writer / integration-tester
│   └── fixer / knowledge-distiller
├── mechanical task gate
├── immutable review packets + finding ledgers
└── explicit safe finish
```

Launch and monitor one writable task:

```bash
.codex/bin/agent-team-worker spawn --run 20260801-auth --task T01 --role developer
.codex/bin/agent-team-worker status --run 20260801-auth --task T01
.codex/bin/agent-team-worker wait --run 20260801-auth --task T01
```

`spawn` refuses a missing/non-root worktree or the wrong branch. The command
uses argv execution, not a shell, and records worker identity under the run.

Before merge, the task record must include successful structured verification
evidence. The gate rejects empty diffs, forbidden or undeclared writes,
undeclared shared surfaces, dirty worktrees, and missing evidence:

```bash
.codex/bin/agent-team-check-task --run .agents/runs/20260801-auth --task T01
```

Merged-task provenance accepts direct ancestry or Git patch-equivalent
cherry-picks. A conflict resolution that changes the patch must be represented
and gated as a dedicated integration-fix task.

## Reviews

Create one packet per independent axis at the exact integration head:

```bash
.codex/bin/agent-team-review --run 20260801-auth create \
  --wave 1 --axis spec --base <sha> --head <sha> \
  --spec-source docs/plans/auth.md
```

Read-only reviewers return structured findings and closure decisions; the
coordinator records them with reviewer identity. Fix tasks address findings.
Each reviewer also writes a bounded result such as
`{"axis":"spec","verdict":"pass","findings":[]}`. Complete the ledger with
that artifact and the independent invocation id:

```bash
.codex/bin/agent-team-review complete \
  --ledger .agents/runs/20260801-auth/reviews/wave-1-spec.json \
  --reviewer spec-reviewer --session-id <review-task-id> \
  --result .agents/runs/20260801-auth/reviews/results/spec.json
```

The required spec and standards axes must have different reviewer identities
and session ids. For small work, each axis runs once on the final diff; for
larger work, once per integrated wave. There is no per-task LLM review, and a
fix re-review reads only the fix diff and necessary finding context.
Required axes must be complete and current:

```bash
.codex/bin/agent-team-review --run 20260801-auth check --wave 1 --head <sha>
```

Require `--require-risk` for concurrency, security, migration, data-loss,
compatibility, auth/permission, or public-API changes.

## Finish

Finish is report-only unless local integration is explicitly requested:

```bash
.codex/bin/agent-team-finish
.codex/bin/agent-team-finish --commit "feat: add auth"
.codex/bin/agent-team-finish --commit "feat: add auth" --push
```

`--push` without `--commit` is rejected. Default branches additionally require
`--allow-default-branch`. After successful integration and optional push, the
state manager atomically changes `READY_TO_FINISH` to `DONE`.
The READY transition itself is rejected until head-bound final verification
and review gates pass and the learning gate is passed or explicitly skipped
with evidence.

## Skill precedence

`qteam-router` routes the work. `agent-team-dev` alone owns execution, workers,
merges, reviews, and finish. `brainstorming`, `grilling`, `grill-with-docs`,
`domain-modeling`, `to-spec`, `to-tickets`, and `wayfinder` may shape decisions
and artifacts but never begin a competing implementation loop.

Developers use `qteam-tdd`; debuggers use `qteam-diagnose`; reviewers use
`qteam-review`.

`goal-execution-discipline` applies across the run but is not a second
orchestrator. It defines what counts as an acceptable change and genuinely
done; QTeam supplies the enforcement mechanisms. Spec-compliance and
code-quality review are always mandatory; the optional `risk` axis can only add
coverage, never replace either one.

Only the bounded Superpowers primitives `brainstorming`, `writing-plans`, and
`verification-before-completion` are installed. Competing orchestration skills
(`using-superpowers`, `executing-plans`, `subagent-driven-development`, and
independent branch finish/worktree flows) are deliberately not installed.

Third-party attribution is in `THIRD_PARTY_NOTICES.md` and `LICENSES/`.
