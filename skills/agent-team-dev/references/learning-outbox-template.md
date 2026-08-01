---
tags: [tools, codex-agent-team-template, skills, agent-team-dev, learning, outbox, qnote, ai]
timestamp: 2026-08-01T00:00:00.000Z
---
# Learning Outbox

The sandbox is scoped to the target repository, so no agent can write to qnote
during a run. Learning capture is a two-step handoff: the distiller writes
proposals into the run's outbox; a qnote-side importer moves approved items
home after the run.

## Layout

```text
.agents/runs/<run-id>/learning-outbox/
├── manifest.json
├── knowledge.md            # one "## <title>" section per knowledge item
├── lessons.md              # one "## <title>" section per lesson
├── skill-proposals/
│   └── <skill-name>.md     # full proposed SKILL.md content or a patch note
└── evidence/
    ├── tests.txt           # verification commands + results
    └── commits.txt         # task commits backing the items
```

## manifest.json

```json
{
  "schema_version": 1,
  "run_id": "20260801-auth-refresh",
  "project": "myrepo",
  "source_commits": ["def456"],
  "items": [
    {
      "id": "K1",
      "title": "Auth middleware ordering constraint",
      "category": "knowledge",
      "source": "T01",
      "file": "knowledge.md",
      "section": "Auth middleware ordering constraint",
      "evidence": ["tests/auth/order.test.ts", "commit def456"],
      "reuse_trigger": "adding middleware to the auth chain",
      "tags": ["auth", "middleware"],
      "confidence": "high",
      "intended_destination": "misc/ai/session-knowledge/knowledge/myrepo-auth-middleware.md",
      "revisit": "none",
      "status": "proposed"
    }
  ]
}
```

`status`: `proposed` (distiller) → `approved` | `rejected` (coordinator, during
the learning gate). The importer only takes `approved` items.

## Rules

- The distiller writes only under the outbox; it never edits source files and
  never claims to have updated qnote or a canonical skill.
- The coordinator approves/rejects during the learning gate and records the
  outcome in `state.json`; rejected items keep their entry (status `rejected`)
  for audit.
- Skill changes are always proposals: the importer lands them under qnote
  `skills/proposals/<skill-name>/`, never over a canonical `SKILL.md`.
- No secrets, no private reasoning traces, no raw noisy logs, no one-off trivia.

## Import (run from qnote root, after the run)

```bash
tools/codex-agent-team-template/bin/import-agent-learning.py <target-repo> <run-id>
```

The importer verifies the manifest schema and that `source_commits` exist in
the target repo, skips non-approved items, dedupes against existing qnote notes
(by destination and title), writes knowledge/lessons to their destinations
under `misc/ai/session-knowledge/`, and writes skill proposals to
`skills/proposals/`. It never overwrites an existing file unless given
`--update`.
