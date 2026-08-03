# QTeam

QTeam is a local, durable software-development orchestrator for Codex. It
combines bounded practices from Superpowers, Matt Pocock's skills, and
Autoresearch while keeping exactly one orchestration authority.

The core rule is simple: roles are created only for distinct permission,
context, output, or lifecycle boundaries; reusable practices remain skills.

## What changed in 0.9

- Task dependencies are now a mechanical execution contract. Every task record
  carries immutable `depends_on` IDs; `task-put`, `PLAN_READY`, and wave start
  reject unknown, cyclic, same-wave, or later-wave prerequisites.
- A task may start only when every predecessor is `merged` or
  `artifact_complete`. Wave start validates the whole wave, and task start
  repeats the check under the state lock so stale plans and task/state status
  projection drift cannot bypass the gate.
- Accepted dependency IDs and wave placement are frozen outside
  `REPLANNING`; a routine `task-put --replace` cannot silently rewrite the DAG.
- `status` reports dependency-ready tasks and exact blockers. During an active
  wave it recommends only tasks from that wave, and the runtime rejects attempts
  to start a future-wave task.
- Unfinished schema-v2/v3/v4 runs migrate conservatively to schema v5. Existing
  active tasks must be replanned with explicit dependencies; migration never
  guesses that a missing historical dependency means “no dependency.” Migration
  validates legacy task/state projections atomically and invalidates any stale
  publication seal.

## What changed in 0.8

- Human dependencies are durable scoped decision gates with one concrete
  question and authority. Task, wave, action, and explicit global scopes block
  only the covered operation; resolution records an auditable allow/deny
  outcome, choice, and evidence. Local integration and publication both seal
  the exact reviewed head and authorization set before Git mutation, so a late
  gate or review writer cannot split branch state from run completion.
- Material task continuations are typed as successor, user-decision, replan,
  or explicit no-followup. Finish rejects ambiguous or unresolved handoffs.
- `agent-team-state status` emits a compact operator packet with exact open
  questions, blocking handoffs, current work, evidence freshness, and one next
  action; the full state is no longer needed for routine updates.
- Feature/bugfix planning assesses twelve scenario dimensions and keeps at most
  one strongest applicable case per dimension, deduplicating overlaps instead
  of multiplying tests or review agents.
- A head-bound public/private boundary gate ignores deletions, scans resulting
  text blobs for committed runtime state, quoted/unquoted credentials, and
  user-specific local paths, and fails closed on unscannable binary changes.
- These interaction-state ideas are adapted from LoopX without importing its
  token/quota economy, claims kernel, or another orchestrator.

## What changed in 0.7

- `qteam-explore` lets QTeam discover evidence-backed paths, ideas, mechanisms,
  and knowledge beyond the options a user already knows to name.
- Exploration reuses the read-only `researcher` role and an `architect`
  falsification pass; it does not add a duplicate persona or orchestrator.
- Deep/broad exploration follows a research-frontier rule: independent breadth
  lanes discover repo-native, external-analog, and adversarial mechanisms;
  evidence-gated candidates receive one-candidate depth traces before architect
  falsification.
- Search remains bounded by selected coverage, at most three live lanes, and
  evidence saturation rather than a separate token-accounting subsystem. Finite
  agent capacity queues lanes; bounded lanes always return complete, disproved,
  or blocked dossiers so a missing source cannot become an indefinite wait or a
  false recommendation. If a lane misses its frozen runtime deadline, the
  coordinator cancels it and records an attributed blocked fallback rather than
  waiting for the worker to cooperate. A hard end-to-end deadline is promised
  only when the worker backend enforces it; unsupported deadlines block before
  launch instead of creating false timing guarantees.
- Mechanically measurable candidates become frozen experiment proposals with a
  replayed base-commit baseline, metric, guard, held-out acceptance check,
  attempt budget, and plateau rule. Any writes still run through normal
  isolated QTeam tasks, applicable TDD, review, and finish gates.

## What changed in 0.6

- QTeam is now a standard Codex plugin in `plugins/qteam`, published by the
  repository-local `qteam` marketplace.
- `./qteam setup [project]` registers the marketplace, installs the plugin, and
  optionally bootstraps the repository runtime in one command.
- `./qteam uninstall [project]` safely removes the optional repository runtime,
  restores its recorded pre-install files, removes the plugin, and unregisters
  the marketplace.
- Skills are supplied by the plugin and are no longer copied into every
  repository. Project setup installs only the permission boundary and runtime:
  read-only role TOMLs, isolated-worker tools, prompts, schemas, and notices.
- Project uninstall is fail-closed. It removes only files whose content still
  matches the installation manifest; locally modified files are retained with
  the recovery manifest and backups.

## What changed in 0.5

- Task facts now drive execution automatically: bounded low-risk slices use
  economy/compact, judgment-heavy or broad work uses standard/full, and named
  high-risk surfaces use deep/risk. Writable workers receive the derived model
  and reasoning profile; review packets receive the matching intensity. Four
  or more otherwise-small tasks make the wave review full without making every
  worker expensive.
- TDD now freezes public behavior seams before implementation and mechanically
  replays each test-only RED commit and minimal GREEN commit. The gate requires
  evidence for every approved seam.
- Diagnosis now freezes a deterministic feedback command and failure pattern,
  replays RED at the reported reproduction commit, requires 3–5 ranked
  falsifiable hypotheses and a causal chain, and rejects leftover marked debug
  instrumentation.
- Spec and code-quality review remain mandatory at every review gate. Compact
  review narrows context to the final diff/contracts/tests; it does not remove
  either independent axis. Risk review is an additional triggered axis.

The 0.4 isolation, transactional state, immutable review ledger, and safe
finish guarantees remain in force:

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
- `qteam-router`, `qteam-explore`, `qteam-tdd`, `qteam-diagnose`, and
  `qteam-review` unify overlapping discovery, Superpowers, and Matt flows.
  Grilling, domain modeling, spec synthesis, ticket slicing, and wayfinding are
  bounded design primitives.
- `goal-execution-discipline` is the standing no-workaround, full-scope,
  evidence, honest-status, and mandatory dual-review contract.

`plugins/qteam/skills/agent-team-dev/SKILL.md` is the normative workflow. This
README is an operator guide.

## Install

Clone this repository once, then install the plugin and set up a target Git
repository with one command:

```bash
git clone https://github.com/qscuio/qteam.git
cd qteam
./qteam setup /path/to/target-git-repository
```

For plugin-only installation, omit the project path:

```bash
./qteam setup
```

The setup command uses Codex's native plugin lifecycle:

```text
codex plugin marketplace add <this-qteam-checkout>
codex plugin add qteam@qteam
```

When a project path is supplied, the project bootstrap records preimages and
content hashes in `.codex/qteam-project.json` before changing project config or
runtime paths, then installs only:

```text
.codex/agents/             read-only roles only
.codex/worker-prompts/     writable role contracts
.codex/bin/                wake, state, worker, gate, review, finish, doctor
.codex/schemas/            run/task/policy/TDD/diagnosis/worker/review schemas
```

QTeam and its bounded Superpowers/Matt-derived primitives come from the plugin;
they are not duplicated under `.agents/skills/`. Use `agent-team-doctor
--smoke` to spend one small Codex call verifying that a named read-only role
loads with `fork_turns=none`.

Uninstall the project runtime and plugin symmetrically:

```bash
./qteam uninstall /path/to/target-git-repository
```

Omit the project path to remove only `qteam@qteam` and its marketplace. If a
managed project file changed after setup, uninstall retains it and stops with
the exact backup directory instead of deleting user work.

Running `setup <project>` again validates and safely removes the previous
runtime before installing the current plugin version. It also recovers a
durable `preparing` or `installing` intent left by an interrupted setup. A
corrupt manifest or locally modified managed file fails closed instead of
reporting a successful update.
Recovery backups, the machine-specific manifest, and run state are added to
the target repository's ignore rules independently so preimages cannot enter a
normal commit.

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
.codex/bin/agent-team-state --run 20260801-auth verify-tdd-cycle T01 \
  --seam request-auth --red-commit <red-sha> --green-commit <green-sha>
.codex/bin/agent-team-state --run 20260801-auth diagnosis-put T01 \
  --file .agents/runs/20260801-auth/worktrees/T01/.qteam-diagnosis.json
.codex/bin/agent-team-state --run 20260801-auth experiment-put T02 \
  --file .agents/runs/20260801-auth/worktrees/T02/.qteam-experiment.json
.codex/bin/agent-team-state --run 20260801-auth verify-task T01
.codex/bin/agent-team-state --run 20260801-auth verify-final \
  --command "pytest -q"
.codex/bin/agent-team-state --run 20260801-auth boundary-check
.codex/bin/agent-team-state --run 20260801-auth status
```

To resume an unfinished legacy schema-version-2/3/4 run,
migrate it atomically first:

```bash
.codex/bin/agent-team-state --run <run-id> migrate-run
```

Historical merged tasks retain their durable provenance. Every unfinished task
is marked `requires_replan` and must be replaced through `task-put` during
`REPLANNING` with explicit dependencies; workers and phase execution reject it
until then.

Register repository-specific shared surfaces at `init` with repeatable
`--shared-surface <glob>`; any task allowed to touch one must be explicitly
declared serial, and the runtime gate checks the actual changed paths.
The run's model names can be configured with `--model-economy`,
`--model-standard`, and `--model-deep`; task workers never override the derived
tier themselves.

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
cherry-picks only when each new integration delta byte-matches the checked task
diff. The contiguous provenance ledger makes direct integration edits fail
closed. A conflict resolution that changes the patch must be represented and
gated as a dedicated integration-fix task.

## Reviews

Create one packet per independent axis at the exact integration head:

```bash
.codex/bin/agent-team-review --run 20260801-auth create \
  --wave 1 --axis spec --base <sha> --head <sha> \
  --spec-source docs/plans/auth.md
```

Read-only reviewers return structured findings and closure decisions; the
runner atomically records a valid `needs-fix` result with reviewer identity.
Fix tasks address findings.
Each reviewer also writes a bounded result such as
`{"axis":"spec","verdict":"pass","findings":[],"resolved_ids":[],"invalid_ids":[],"upheld_ids":[],"invalid_evidence":{}}`. Launch the reviewer through
the read-only runner so the actual model, reasoning, packet digest, invocation,
and output are captured:

```bash
.codex/bin/agent-team-review run \
  --ledger .agents/runs/20260801-auth/reviews/wave-1-spec.json \
  --reviewer spec-reviewer --session-id <review-task-id>
.codex/bin/agent-team-review complete \
  --ledger .agents/runs/20260801-auth/reviews/wave-1-spec.json \
  --receipt .agents/runs/20260801-auth/reviews/receipts/<review-task-id>.json
```

The required spec and standards axes must have different reviewer identities
and session ids. For small work, each axis runs once on the final diff; for
larger work, once per integrated wave. There is no per-task LLM review. A fresh
serial `FIXING` task owns each confirmed valid finding; a non-empty fix re-review
reads only its diff and frozen finding context. A claimed false positive instead
uses an unchanged-HEAD dispute re-review. Only fresh receipts close either set.
Required axes must be complete and current:

```bash
.codex/bin/agent-team-review --run 20260801-auth check --wave 1 --head <sha>
```

Review creation rejects waves absent from the derived run policy. At the gate,
each wave packet must cover its recorded merge commits; `base == head` cannot
stand in for reviewing changed code, and an old risk receipt cannot cover a
later high-risk merge.

Concurrency, security, migration, data-loss, compatibility, auth/permission,
and public-API tasks automatically require the risk axis. `--require-risk`
remains available to conservatively force it for an otherwise unflagged run.

## Finish

Finish is report-only unless local integration is explicitly requested:

```bash
.codex/bin/agent-team-finish
.codex/bin/agent-team-finish --integrate
.codex/bin/agent-team-finish --integrate --push
```

`--push` without `--integrate` is rejected. Default branches additionally require
`--allow-default-branch`. Before touching Git, local integration seals the exact
reviewed head and its gate/review authorization under the run lock; `--push`
additionally requires the scoped publish authorization. After successful
integration and optional push, the state manager atomically changes
`READY_TO_FINISH` to `DONE`.
The READY transition itself is rejected until head-bound final verification,
review, and public-boundary gates pass; the learning gate is passed or
explicitly skipped with evidence; and every typed handoff is closed.

## Skill precedence

`qteam-router` routes the work. `agent-team-dev` alone owns execution, workers,
merges, reviews, and finish. `brainstorming`, `grilling`, `grill-with-docs`,
`domain-modeling`, `qteam-explore`, `to-spec`, `to-tickets`, and `wayfinder` may
shape decisions and artifacts but never begin a competing implementation loop.

The coordinator uses `qteam-explore` when the solution frontier is unknown;
researchers execute only its frozen, cold evidence-lane packets;
developers use `qteam-tdd`; debuggers use `qteam-diagnose`; reviewers use
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

Third-party attribution, including LoopX interaction-state ideas, is in
`plugins/qteam/THIRD_PARTY_NOTICES.md` and `plugins/qteam/LICENSES/`.
