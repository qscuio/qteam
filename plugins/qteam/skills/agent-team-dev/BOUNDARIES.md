# Code Quality Workflow BOUNDARIES

## State ownership — exactly one writer per fact

| fact | owner | sole write path | readers |
|---|---|---|---|
| approved task behavior and scope | QTeam task record | `agent-team-state task-put` | worker, gates, reviewers |
| task implementation diff | assigned task worktree | assigned writable worker | checker, integrator, reviewers |
| cleanup practice | `deslop/references/diff-cleanup.md` | QTeam plugin release | public skill, project setup |
| cleanup obligation | worker packet derivation | `agent-team-worker.py` packet build | applicable worker |
| structural quality rubric | thermo skill reference | QTeam plugin release | public skill, project setup |
| installed practice/rubric copies | project runtime manifest | setup/refresh only | worker, packet builder, doctor |
| structural defect finding | standards finding ledger | `agent-team-review run` receipt transaction | coordinator, fixer, re-reviewer |
| finding closure | independent re-review receipt | `agent-team-review complete` | review gate, finish gate |

`deslop` creates no completion fact or self-attested pass bit. Behavior evidence
remains owned by the existing verification record. Runtime practice/rubric
files are byte-identical installed copies, not second authorities.

## Dependency edges — whitelist

deslop reference -> project setup -> `.codex/practices/deslop.md`
thermo reference -> project setup -> `.codex/standards/structural-quality.md`
`agent-team-worker.py` -> task role/work-kind -> one cleanup obligation
assigned worker -> owned diff -> existing focused verification
`agent-team-review.py` -> installed rubric snapshot -> immutable standards packet
immutable standards packet -> standards finding ledger
`qteam-harden` -> frozen deterministic quality commands only
public quality skills -> existing QTeam worker/review workflow when a run exists

No quality skill writes run state, creates a reviewer, closes a finding, changes
task scope, pushes, publishes, or owns CI/PR state.

## Vocabulary

| concept | project word | authority |
|---|---|---|
| behavior-preserving local cleanup of an owned diff | deslop | public skill |
| rules for architecture and maintainability damage | structural quality rubric | thermo skill reference |
| deterministic post-integration property check | quality lane | `qteam-harden` |
| independent code-quality judgment | standards review | `qteam-review` |
| concrete review-blocking defect | finding | review ledger |
| design expansion beyond frozen task authority | replanning | QTeam state machine |

Do not introduce `cleanup gate`, `thermo gate`, `quality reviewer`, or another
synonym that implies a second authority.

## Non-goals

- No CI, PR, publication, or GitHub workflow.
- No additional worker/reviewer role, review axis, or durable state field.
- No repository-wide cleanup from a bounded task.
- No style-only finding and no automatic 1000-line failure.
- No behavior change, test weakening, scope expansion, or self-review closure.
