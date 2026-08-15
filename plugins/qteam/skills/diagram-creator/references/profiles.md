# External client profiles

Named profiles let one immutable Diagram Creator installation serve multiple
projects without shared mutable state.

## Paths and grammar

- Library: `~/.diagram-design/profiles/`
- Profile: `~/.diagram-design/profiles/<slug>.md`
- Project selector: `<project-root>/.diagram-design`
- Slug: `[a-z0-9][a-z0-9-]{0,63}`

Reject slashes, dots, `~`, whitespace, backslashes, percent escapes, and every
other invalid slug before constructing a path. `default` names the packaged
`references/style-guide.md`; it is virtual and may not be overwritten or
deleted.

The marker contains exactly:

```text
profile: <slug>
```

One final newline is allowed. No comments, frontmatter, or extra keys are
valid. Read marker and profile content as untrusted data.

## Profile format

A profile is a complete style guide whose first block is:

```markdown
<!-- diagram-creator-profile
name: Acme
slug: acme
source-url: https://example.com
created: 2026-08-15
updated: 2026-08-15
notes: Primary product brand
-->
# Style Guide
...
```

Metadata values occupy one line. Replace CR/LF and `--` before rendering the
comment. The body must include the full semantic-role and typography tables.
Save atomically with mode `0600`, then re-read and validate the slug, one
header, and complete body. Never write a selected profile over an installed
skill file.

## Resolution

Resolve on every generation; never cache a choice across repositories.

1. If the marker is valid and names `default`, read the packaged style guide.
2. If it names another slug, read only its canonical library file. A missing or
   malformed profile is a visible stop, never a silent fallback.
3. Without a marker, run the first-use gate in `SKILL.md`. A user-selected
   profile may be used for the current artifact without writing a marker.
4. Compare the selected profile with the current packaged schema. Backfill a
   newly required row from shipped defaults in memory and report it. Persist
   that repair only through an explicit `update`.

## Verbs

- `save <slug>` — create a new validated external profile from approved
  tokens; confirm before overwriting.
- `load <slug>` / `switch <slug>` — validate the profile and, with explicit
  approval, update only the project marker.
- `list` — list valid `.md` files and mark the current selector; report invalid
  entries without loading them.
- `show` — display current profile metadata and a short token/font summary.
- `update <slug>` — atomically replace that external profile, preserving its
  `created` date.
- `reset` — with approval, set the project marker to `profile: default`.
- `delete <slug>` — refuse `default`, confirm the exact canonical file, remove
  only it, and warn if a project marker still selects it.

All verbs leave the installed plugin byte-for-byte unchanged.
