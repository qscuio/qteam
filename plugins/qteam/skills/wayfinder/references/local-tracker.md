# Local Markdown tracker fallback

Use this fallback only when no connected issue tracker exists and one session
at a time owns wayfinding. Copy `local-map-template.md` to
`docs/wayfinder/<destination-slug>.md`; the slug must match
`[a-z0-9][a-z0-9-]{0,63}`. Commit it so decisions survive session changes.

Each ticket is one table row with these fields:

- **Name** — a unique readable anchor/link in the same document;
- **Type** — `research`, `prototype`, `grilling`, or `task`;
- **Mode** — `AFK` or `HITL`;
- **Status** — `open` or `closed`;
- **Owner** — `-` when unclaimed, otherwise one stable session/owner label;
- **Blocked by** — comma-separated ticket names or `-`;
- **Question / resolution** — a precise question while open, then one bounded
  resolution and evidence pointer when closed.

The frontier is mechanically determined from the table: rows whose Status is
`open`, Owner is `-`, and every Blocked-by row is `closed`. Re-read the file
from disk immediately before claiming. Claim by changing only Owner and
committing that edit before doing work. Close by recording the bounded
resolution/evidence, setting Status to `closed`, and updating the map's linked
gist in the same commit.

Validate before every commit:

1. Ticket names are unique and every blocker names another row.
2. No ticket blocks itself and the blocking graph is acyclic.
3. A closed row has a resolution/evidence pointer; an open row has a question.
4. A HITL resolution records the human answer, never an agent guess.
5. Decisions-so-far links each closed row exactly once.

Git does not make a read/edit claim atomic. On concurrent ownership, merge
conflict, or uncertain remote freshness, stop using this fallback and move the
map to a tracker with native claims and dependency edges.
