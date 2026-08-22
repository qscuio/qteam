# QTeam

QTeam is a local, durable software-development orchestrator used inside Codex,
Claude Code, or Cursor sessions. Its current isolated worker/reviewer backend
is Codex, while its durable goal and repository runtime are host-neutral. It
combines bounded practices from Superpowers, Matt Pocock's skills, and
Autoresearch while keeping exactly one orchestration authority.

The core rule is simple: roles are created only for distinct permission,
context, output, or lifecycle boundaries; reusable practices remain skills.

## What changed in 0.17

- `$deslop` adds a bounded, behavior-preserving cleanup pass after GREEN. QTeam
  workers receive the obligation centrally for implementation work, preserve
  RED/GREEN history, stay inside the task write set, and rerun focused
  verification after any cleanup.
- `$thermo-nuclear-code-quality-review` adds a strict structural-quality rubric
  for incidental complexity, spaghetti growth, unearned abstractions, unclear
  boundaries, canonical ownership, and cohesive file growth. Every standards
  review packet freezes the installed rubric and binds it into the existing
  immutable packet digest; no new reviewer or review axis is introduced.
- Project setup installs and manifest-binds both runtime references plus the
  Cursor Team Kit MIT license. This release intentionally adds no CI, PR, or
  GitHub automation.

## What changed in 0.16

- `$diagram-creator` now separates structural semantics from visual treatment
  with Diagram Contract v1. The strict model is embedded in the same offline
  HTML artifact and owns element identities, labels, kinds, relationship
  endpoints, and relationship kinds; SVG projections repeat only IDs and
  rendered bounds/routes.
- A dependency-free `diagram_contract.py` CLI validates the model before
  layout, binds every modeled fact to exactly one SVG projection, and emits a
  deterministic composition report. Fixed `standard` and `showcase` profiles
  measure overlap, spacing, orthogonality, bend count, route stretch,
  micro-segments, containment, and crossings without allowing an artifact to
  weaken its own limits.
- The first contract-backed path applies to static architecture, data-flow,
  process/swimlane, ER, tree/org/nested/layer, and UML class/component/deployment
  figures. Sequence, state, flowchart, use-case, activity, loop, animated, and
  quantitative figures keep their native semantic or data authority until a
  specialized contract can represent their lifelines, curves, diamonds,
  actors, bars, loops, or time states faithfully.
- The implementation is a clean-room QTeam design informed by the versioned
  Diagram IR, semantic-contract, composition, and unified-validation ideas in
  `yizhiyanhua-ai/fireworks-tech-graph` at commit
  `d56d45a286f16439a0fba2e66ff85f598c42ef41`. QTeam retains its existing
  editorial HTML source, visual system, type references, and self-contained
  output instead of importing a second renderer.

## What changed in 0.15

- `$isometric` turns a repository into an interactive, evidence-backed
  architecture city. Structures, dependency edges, external systems,
  drill-down views, and request traces are driven by a bounded JSON packet;
  every visible claim resolves to repository-relative files and SHA-256
  evidence that can be recomputed against an exact Git source snapshot.
- The artifact is a self-contained offline HTML file with a fixed packaged
  rendering engine, deterministic hash-addressed views, keyboard navigation,
  reduced-motion behavior, and a complete static/print fallback. Its validator
  rejects engine/markup changes, overlapping footprints, unresolved evidence,
  sidecar or network resources, stale Git identity, and common share-safety
  leaks.
- This is a clean-room QTeam-native design informed by the useful concept in
  `sayantan94/toolbelt` isometric at commit
  `419388cf0e15d1741d4cfe0fdc9237cd3eef2be5`; no upstream engine, template, or
  validator code is vendored. QTeam deliberately removes the arbitrary
  15-structure minimum and fixed-subagent rule: map fidelity follows measured
  repository facts, using existing researcher/architect roles only when an
  active run actually needs them.
- The skill has a sharp routing boundary: use `$isometric` for a whole-repo
  architecture map, `$diagram-creator` for one focused technical/UML visual,
  and `$show-me` when the primary goal is interactive teaching.

## What changed in 0.14

- `$diagram-creator` vendors Diagram Design 2.4.0's editorial HTML/SVG system
  and adds bounded UML class, use-case, component, deployment, and activity
  notation. It also retains sequence/state, architecture, data, process,
  draw.io, Mermaid, export, brand-profile, and accessible-motion workflows.
- `$show-me` creates self-contained interactive teaching UIs. Lessons are
  model-first, manually stepable, keyboard accessible, reduced-motion safe,
  printable, and understandable without JavaScript; animation exposes cause
  and state change instead of decorating a static diagram.
- `$handoff` adapts Matt Pocock's latest compact, temporary, secret-redacted
  continuation packet and binds it to QTeam's durable goal checkpoint and Git
  evidence. It never replaces typed run handoffs or durable state.
- `$wayfinder` now carries the latest map-as-index, named-ticket, native
  dependency frontier, claim, fog-of-war, out-of-scope, and HITL/AFK rules.
  It still hands clear decisions to `$to-spec` or a frozen QTeam epic rather
  than becoming an implementation orchestrator.
- All four capabilities ship through the same Codex, Claude Code, and Cursor
  plugin. Project setup also installs Diagram Design's MIT license and rejects
  conflicting repository-local copies of the new skill names.

## What changed in 0.13

- `qteam-goal` projects the existing durable run into a bounded host completion
  condition for Codex goals, Claude Code `/goal`, or a Cursor stop hook. The
  native facility keeps a session taking turns; only QTeam state, Git heads,
  gates, and evidence can prove completion.
- `agent-team-goal wait --after <checkpoint>` blocks inside one tool call until
  durable state, worker output, or review receipts change. This avoids repeated
  model-turn polling. Native teammate messages/hooks may wake sooner, while the
  checkpoint makes missed notifications recoverable.
- Main-session rotation is now an explicit phase-boundary choice, not a long-run
  requirement. Continue by default; clear, hand off, subagent, or compact only
  when the next phase actually benefits. Fresh sessions resume from durable
  status and artifacts rather than transcript memory.
- The bounded workflow primitives were refreshed from Superpowers 6.3 and the
  latest Matt Pocock skills: spike/bounded/architectural design routing,
  dependency-frontier grilling, fresh-context ticket sizing, expand/migrate/
  contract refactors, durable file handoffs, primary-source research, secret-
  redacted diagnosis, and deep-module test-seam vocabulary. QTeam deliberately
  rejects the upstream option to park a valid late review finding.
- QTeam now ships Claude Code and Cursor plugin manifests plus host-neutral
  `runtime-setup`/`runtime-uninstall` commands. This does not pretend the current
  Codex worker backend is already a Claude/Cursor process launcher.

## What changed in 0.12

- Task facts now derive a `lean`, `standard`, or `hardened` workflow shape in
  addition to model/review policy. The shape conditionally triggers refactor,
  mutation/property/fault hardening, and public-surface QA lanes. Each lane is
  a frozen, deduplicated command set replayed on the exact integration HEAD;
  it is not another per-task LLM review. A project `.qteam/policy.json` may
  only raise the shape floor or add lane triggers, and init freezes both core
  and project policy digests.
- A coordinator-owned durable priority queue can claim equal-priority work in
  bounded batches. It improves scheduling and visibility without enabling
  peer-to-peer agent routing: the existing dependency DAG, phases, WAL, task
  records, and reviews remain the only delivery authority.
- QTeam now ships a dependency-free local Web control plane with run/wave/DAG,
  worker, quality, decision, queue, review, and redacted event views. It uses
  SSE locally, allows only fixed existing CLI actions, requires CSRF on writes,
  keeps raw logs off by default, and binds only numeric loopback. Remote use is
  through an SSH tunnel or exact trusted HTTPS proxy with a mode-0600 token.
- Herdr is supported as an optional display/session adapter. It can open the
  QTeam Web UI or compact watch stream in a Herdr pane, but never launches
  workers/reviewers or owns state. QTeam has no tmux dependency or fallback.
- These adaptive-shape, batch, and layered-policy ideas are a clean-room
  adaptation of useful SwarmForge concepts; QTeam keeps its graph execution,
  isolation, mandatory dual review, and durable gates instead of adopting a
  fixed linear role pipeline.

## What changed in 0.11

- Writable workers and reviewer runners now use Codex JSONL mode and retain a
  compact trajectory summary: tool-call counts, failed/empty/duplicate-call
  anomalies, token usage, execution identity, runner version, and a trace
  digest. Raw commands, tool output, and private reasoning stay out of review
  packets, so trajectory evidence does not turn quality review into a token-heavy
  log replay.
- Review execution is pinned separately from worker execution by model,
  reasoning effort, provider, family, and Codex CLI version. Every review packet
  includes two transparent axis-specific calibration canaries; these catch
  inconsistent grading but are not secret/adversarial benchmarks. A receipt is
  invalid if the judge cannot classify them correctly. Packets state whether generator and
  judge families are actually independent instead of merely assuming they are.
- Task facts now derive a reversibility class that a declaration may raise but
  never lower: `contained-reversible`, `wide-reversible`, or
  `hard-to-reverse`. The corresponding lane is frozen in task/wave policy;
  hard-to-reverse delivery additionally requires an allowed user `finish`
  decision bound to the exact integration head and hard-task policy snapshot.
- Confirmed user corrections, trajectory anomalies, review findings, rollbacks,
  and tool failures can become typed eval cases. Harvest and import verify the
  referenced regular file, run identity, exact evidence hash, and coordinator
  approval event before an approved case enters durable eval knowledge. The
  distiller can create candidates but cannot approve them. This is trace-to-eval, not raw
  session-memory accumulation.

Approve a harvested eval explicitly before import:

```bash
.codex/bin/agent-team-state --run <run-id> learning-item-decision <item-id> \
  --outcome approved --evidence '<bounded coordinator evidence>'
```

## What changed in 0.10

- Spec review now begins with a deterministic artifact preflight. Typed QTeam
  specs fail fast on missing required sections, IDs, duplicate definitions, and
  unsupported markers; legacy sources remain compatible with an explicit
  warning. The immutable lint report is frozen into the review packet, and the
  LLM reviewer is told not to repeat passing mechanical checks.
- `wayfinder` can freeze a large destination as one durable epic manifest above
  normal QTeam runs. The manifest validates the complete cross-run DAG and
  stable owner/consumer contracts. `agent-team-state init --epic` mechanically
  blocks a run until its predecessors are durably `DONE` and its starting commit
  contains every predecessor's recorded finished head. A started plan is frozen.
- Large legacy repositories may use a researcher-authored component index whose
  source entries are sealed to a base commit and exact Git blobs. A stale source
  blocks index use instead of feeding old summaries to later agents.
- Post-implementation spec reconciliation is proposal-only. Drift reports bind
  the durable integration head, source hashes, and user-owned `finish` decision
  gates. Sealing binds each still-open decision to the exact proposed change;
  approval happens afterward, and finish rechecks the registered report. A
  report never silently rewrites approved requirements, design, or tickets.
- These artifact workflow ideas are adapted from Smart Ralph without importing
  its stop-hook execution loop, roles, mutable state authority, or POC-first test
  deferral.

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

Claude Code gets the same native plugin plus repository runtime with the same
command shape:

```bash
/path/to/qteam/qteam setup --host claude /path/to/target-git-repository
/path/to/qteam/qteam uninstall --host claude /path/to/target-git-repository
```

Cursor Agent currently exposes `--plugin-dir` but no non-interactive persistent
plugin-install command. QTeam therefore keeps its lifecycle honest: setup and
uninstall manage the repository runtime, while the launcher loads the exact
local plugin on every session without a stale host cache:

```bash
/path/to/qteam/qteam setup --host cursor /path/to/target-git-repository
/path/to/qteam/qteam cursor /path/to/target-git-repository
/path/to/qteam/qteam uninstall --host cursor /path/to/target-git-repository
```

For an IDE-persistent Cursor install, use `/add-plugin`; until Cursor provides
a non-interactive install/remove CLI, QTeam does not claim that UI mutation was
automated. The repository runtime remains host-neutral and may also be managed
alone with `runtime-setup` / `runtime-uninstall`.

For an existing run, generate the exact session adapter instead of duplicating
the goal in prose:

```bash
./qteam goal /path/to/repo --run <run-id> condition --host claude
./qteam goal /path/to/repo --run <run-id> condition --host codex
./qteam goal /path/to/repo --run <run-id> condition --host cursor
```

Claude uses the returned `/goal` command. Codex creates/updates its native goal
with the returned condition when that tool is available. Cursor installs the
returned command as a project `stop` hook. These are continuation adapters, not
new state authorities.

Codex setup uses its native plugin lifecycle:

```text
codex plugin marketplace add <this-qteam-checkout>
codex plugin add qteam@qteam
```

Claude setup analogously uses `claude plugin marketplace add` plus
`claude plugin install/update`, verifies the installed 0.17 version, and its
uninstall command removes both registrations.

Start the Web UI after project setup:

```bash
./qteam serve /path/to/target-git-repository --run <run-id>
```

Without a token this is a redacted, read-only operator view. To enable fixed
control actions, provide a private token even on loopback:

```bash
umask 077
printf '%s\n' '<at-least-32-random-characters>' > /secure/path/qteam.token
./qteam serve /path/to/repo --run <run-id> \
  --token-file /secure/path/qteam.token
```

It binds loopback only and validates Host/Origin to resist DNS rebinding. For a
remote operator, prefer an SSH tunnel. An HTTPS reverse proxy on the same host
may be explicitly trusted only with its exact external origin and a private
token file; the built-in server never becomes an Internet-facing listener:

```bash
chmod 600 /secure/path/qteam.token
./qteam serve /path/to/repo --run <run-id> --host 127.0.0.1 \
  --token-file /secure/path/qteam.token \
  --trusted-origin https://qteam.example.com
```

Raw worker logs are intentionally absent unless `--allow-raw-logs` is passed;
the default UI shows bounded execution identity and lifecycle summaries.

For the optional Herdr display adapter, first verify Herdr and then run QTeam
from inside a Herdr-managed pane:

```bash
./qteam herdr doctor
./qteam herdr open /path/to/repo --run <run-id> --mode web
```

Use `--mode watch` for a compact terminal stream. Herdr is not installed by
QTeam and is never an orchestration dependency.

Projects may strengthen the core adaptive policy with `.qteam/policy.json`:

```json
{
  "schema_version": 1,
  "workflow_shape_floor": "standard",
  "required_quality_lanes": {
    "refactor": {"work_kinds": [], "risk_flags": []},
    "hardening": {"work_kinds": [], "risk_flags": ["security"]},
    "public-surface-qa": {"work_kinds": [], "risk_flags": ["public-api"]}
  }
}
```

The layer cannot disable TDD, diagnosis, review, risk, reversibility, or any
other core gate.

When a project path is supplied, the project bootstrap records preimages and
content hashes in `.codex/qteam-project.json` before changing project config or
runtime paths, then installs only:

```text
.codex/agents/             read-only roles only
.codex/worker-prompts/     writable role contracts
.codex/bin/                wake, state, worker, gate, review, finish, doctor
.codex/schemas/            run/task/artifact/epic/index/drift/review schemas
.codex/qteam-ui/           local static Web assets (no external CDN/runtime)
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
runtime before installing the current plugin version. The exact old runtime
is first sealed into a fsynced, content-digested `.codex/qteam-refresh/`
intent. A later setup verifies every saved file, mode, manifest, and backup
tree before restoring that snapshot after SIGKILL or power loss. Once the new
project runtime is installed, plugin installation becomes a durable
roll-forward phase: a failed or partially applied plugin command leaves the
intent in place. The exact continuation and expected plugin version are frozen;
the next setup idempotently completes that command and verifies the installed,
enabled version before clearing the intent, instead of silently pairing
different versions. A pre-intent snapshot orphan is removed
only after the still-installed runtime and its preimage pass full integrity
checks. Setup also recovers durable `preparing` or `installing` intents left by
an interrupted first install. A corrupt snapshot, manifest, or locally
modified managed file fails closed instead of reporting a successful update.
Recovery backups, the machine-specific manifest, and run state are added to
the target repository's ignore rules independently so preimages cannot enter a
normal commit.

## Visual explanation skills

Use `$isometric` when the durable artifact should explain the architecture of
an entire repository as an explorable map:

```text
Use $isometric to map this repository, including its request path and external systems.
```

It emits one offline HTML file backed by a structured fact/evidence packet.
Run its validator with `--repo <exact-worktree>` to re-hash every cited source,
source HEAD, and source dirty-state claim. An in-repository map may be committed
after that snapshot only when the intervening commit changes the map alone. It is not the right tool for a single UML
relationship or for a time-driven lesson.

Use `$diagram-creator` when the durable artifact is a static technical or
product visual:

```text
Use $diagram-creator to make a UML component diagram of this service boundary.
```

It produces self-contained HTML with inline accessible SVG and can export SVG
or PNG on request. Static figures in Diagram Contract v1's documented scope
embed the strict model and must pass semantic/SVG binding plus deterministic
composition checks; specialized or animated types retain their native
authority. UML output follows an explicitly bounded notation subset; it does
not require PlantUML and does not claim to emit XMI or a fully machine-verifiable UML model.

Use `$show-me` when the learner needs to control time, inputs, or state:

```text
Use $show-me to teach me how retries and backoff evolve, step by step.
```

It produces a self-contained interactive HTML lesson with Back/Next/Reset,
optional user-initiated playback, narration, invariants, keyboard controls,
reduced-motion behavior, and a static/print fallback. Static figures inside a
lesson may follow `$diagram-creator`, but the two skills have separate trigger
and quality contracts.

Use `$handoff` only when actually changing session, harness, or owner. It saves
a private temporary pointer document and, for active QTeam work, includes the
fresh goal checkpoint rather than copying the transcript or run state.

## Start or resume

### In-session goals and notifications

QTeam does not require a second supervisor session. The active Codex, Claude
Code, or Cursor conversation can own coordination while the run remains durable
on disk. Check it with:

```bash
.codex/bin/agent-team-goal --run <run-id> status
```

Notification has two layers. Codex native subagent delivery, Claude Agent Team
messages, and Cursor hooks are push accelerators when the host supplies them.
The portable floor is a checkpointed blocking wait:

```bash
.codex/bin/agent-team-goal --run <run-id> wait \
  --after <checkpoint> --timeout 300
```

Internally this is a bounded long-poll over durable files; externally it is one
sleeping tool call that returns only on a change, terminal human gate, or
timeout. It therefore consumes no repeated model turns. Every wake is followed
by a fresh status read, so a missed host push is harmless.

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
.codex/bin/agent-team-artifact lint --kind spec --file docs/plans/auth.md
.codex/bin/agent-team-state --run 20260801-auth phase SPEC_READY
.codex/bin/agent-team-state --run 20260801-auth task-put --file /tmp/T01.json
.codex/bin/agent-team-state --run 20260801-auth verify-tdd-cycle T01 \
  --seam request-auth --red-commit <red-sha> --green-commit <green-sha>
.codex/bin/agent-team-state --run 20260801-auth diagnosis-put T01 \
  --file .agents/runs/20260801-auth/worktrees/T01/.qteam-diagnosis.json
.codex/bin/agent-team-state --run 20260801-auth experiment-put T02 \
  --file .agents/runs/20260801-auth/worktrees/T02/.qteam-experiment.json
.codex/bin/agent-team-state --run 20260801-auth verify-task T01
.codex/bin/agent-team-state --run 20260801-auth quality-assess \
  --wave 1 --lane refactor --outcome not-needed \
  --rationale "post-GREEN inspection found no smaller ownership boundary"
.codex/bin/agent-team-state --run 20260801-auth quality-check \
  --wave 1 --lane hardening
.codex/bin/agent-team-state --run 20260801-auth verify-final \
  --command "pytest -q"
.codex/bin/agent-team-state --run 20260801-auth boundary-check
.codex/bin/agent-team-state --run 20260801-auth status
```

Tasks whose derived policy names a quality lane must freeze that lane's
deterministic commands in `quality_commands` before execution. `$qteam-harden`
defines the refactor, adversarial-hardening, and public-consumer evidence
contract without adding another per-task reviewer. Refactor also requires one
bounded wave-level post-GREEN assessment, either a durable not-needed rationale
or an integrated same-wave refactor task. The state manager deduplicates
wave commands, runs them in a disposable detached checkout of the exact
integration HEAD, hashes both output streams, and rechecks the files and HEAD
before review and finish. Commands must bootstrap from tracked sources and
repository-native caches; ignored integration-only dependencies are not inherited.

For coordinator-owned batch work, enqueue a typed record and claim only the
highest-priority equal-priority group:

```bash
.codex/bin/agent-team-state --run 20260801-auth queue-put --file /tmp/Q01.json
.codex/bin/agent-team-state --run 20260801-auth queue-claim \
  --consumer coordinator --limit 4
.codex/bin/agent-team-state --run 20260801-auth queue-complete Q01 \
  --consumer coordinator --outcome completed --evidence 'merged as T03'
```

The queue improves scheduling and UI visibility; it cannot bypass task
dependencies, worker isolation, merge gates, or the mandatory spec and
standards review axes.

For a multi-run effort, let `wayfinder` create and validate the portfolio, then
bind each run to its mechanical dependency gate:

```bash
.codex/bin/agent-team-artifact epic-init --epic platform --goal "ship platform"
.codex/bin/agent-team-artifact epic-plan --epic platform --file /tmp/platform.json
.codex/bin/agent-team-state --run foundation init --goal "foundation" --epic platform
.codex/bin/agent-team-artifact epic-complete-run --epic platform --run foundation
```

To resume an unfinished schema-version-2/3/4/5 run, or a schema-version-6 run
whose policy-v2/additive/core-layer contract predates the installed runtime,
migrate it atomically first:

```bash
.codex/bin/agent-team-state --run <run-id> migrate-run
```

Finished or publication-sealed runs cannot migrate, and active workers/reviews
must be settled first. Historical merged tasks retain their durable provenance
and previously required quality lanes. Every unfinished task
is marked `requires_replan` and must be replaced through `task-put` during
`REPLANNING` with explicit dependencies; workers and phase execution reject it
until then.

Register repository-specific shared surfaces at `init` with repeatable
`--shared-surface <glob>`; any task allowed to touch one must be explicitly
declared serial, and the runtime gate checks the actual changed paths.
The run's worker model names can be configured with `--model-economy`,
`--model-standard`, and `--model-deep`; reviewer models are independently
configured with `--review-model-economy`, `--review-model-standard`, and
`--review-model-deep`. Workers and reviewers never override the frozen derived
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
`{"axis":"spec","verdict":"pass","trajectory_verdict":"pass","calibration_results":{"cal-spec-01":"pass","cal-spec-02":"needs-fix"},"findings":[],"resolved_ids":[],"invalid_ids":[],"upheld_ids":[],"invalid_evidence":{}}`. Launch the reviewer through
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
`domain-modeling`, `qteam-explore`, `to-spec`, `to-tickets`, `wayfinder`,
`isometric`, `diagram-creator`, `show-me`, and `handoff` may shape decisions and artifacts
but never begin a competing implementation loop.

The coordinator uses `qteam-explore` when the solution frontier is unknown;
researchers execute only its frozen, cold evidence-lane packets;
developers use `qteam-tdd`; debuggers use `qteam-diagnose`; reviewers use
`qteam-review`.

`goal-execution-discipline` applies across the run but is not a second
orchestrator. It defines what counts as an acceptable change and genuinely
done; QTeam supplies the enforcement mechanisms. Spec-compliance and
code-quality review are always mandatory; the optional `risk` axis can only add
coverage, never replace either one.

Typed specs and tickets use cheap deterministic lint before semantic work.
Mandatory quality review remains wave-level: the lint report narrows reviewer
attention but never replaces either the spec or standards axis. Freshness-bound
code indexes are optional researcher evidence for large legacy repositories;
epic manifests and drift proposals remain artifacts under the same coordinator.

Only the bounded Superpowers primitives `brainstorming`, `writing-plans`, and
`verification-before-completion` are installed. Competing orchestration skills
(`using-superpowers`, `executing-plans`, `subagent-driven-development`, and
independent branch finish/worktree flows) are deliberately not installed.

Third-party attribution, including Diagram Design and its bundled icon sources,
is in
`plugins/qteam/THIRD_PARTY_NOTICES.md` and `plugins/qteam/LICENSES/`.
