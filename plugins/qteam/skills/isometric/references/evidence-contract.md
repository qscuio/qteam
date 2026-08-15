# Evidence and data contract

The template contains exactly one JSON packet with these top-level fields:

```text
schema_version, sample_data, repository, title, subtitle, stats, groups,
structures, edges, externals, trace, overview, evidence
```

## Repository identity

```json
{
  "source_head_sha": "40 or 64 lowercase hex characters",
  "source_dirty": false,
  "scope": "bounded description of what was scanned"
}
```

The validator's `--repo` mode recomputes every evidence source digest. For an
external map, `source_head_sha` must equal the repository HEAD. For a map stored
inside the repository, the source snapshot may be an ancestor only when every
committed path since that snapshot is the exact map artifact; this avoids the
self-referential impossibility of embedding the map's own commit SHA. Any later
source change makes the map stale. `source_dirty` is the worktree dirty state
after excluding the map artifact itself; it records observation and never
weakens digest validation. Both SHA-1 and native SHA-256 Git object formats are
accepted.

## Evidence ledger

Each entry has exactly:

```json
{
  "id": "E-entry",
  "claim": "The HTTP entry point registers the public routes.",
  "sources": [
    {"path": "src/http/routes.py", "sha256": "64 lowercase hex characters"}
  ],
  "measurement": null
}
```

For measured values, replace `measurement` with:

```json
{
  "command": "scc src --include-ext py",
  "result": "4,812 source lines",
  "exclusions": "generated, vendor, caches, lockfiles"
}
```

Paths are canonical repository-relative paths. They cannot be absolute, contain
`..`, use backslashes, resolve through symlinks, or name non-regular files.

Every evidence ID must be referenced by at least one visible claim, and every
visible claim must carry one or more evidence IDs.

## Static summary grammar

The marked `#isometric-static-summary` region is data, not free-form HTML. Keep
this exact grammar so the no-JavaScript and print fallback remain safe and
mechanically comparable:

```html
<h2>Exact data title</h2>
<p><strong>Invariant:</strong> Exact overview invariant</p>
<ul><li>First structure name</li><li>Second structure name</li></ul>
```

Use exactly one `h2`, one `p`, one `strong`, one `ul`, and one `li` per
structure. These tags have no attributes. Do not add comments, headings,
links, images, styles, scripts, event handlers, or other markup.

## Visible records

- `stats`: `label`, `value`, `evidence_ids`.
- `groups`: `id`, `name`, `color` (`#RRGGBB`).
- `structures`: `id`, two-character `code`, `name`, `group`, numeric `size`,
  integer `position`, positive integer `footprint`, `height`, `kind`, `what`,
  `how`, `talks`, `evidence_ids`, and `children`.
- `children`: `id`, two-character `code`, `name`, `what`, `how`,
  `evidence_ids`.
- `edges`: `from`, `to`, `label`, `kind`, `evidence_ids`; optional `via` is an
  array of integer grid points. Self-loops are unsupported because the map
  represents relationships between viewer-level structures.
- `externals`: `id`, `name`, `target`, `label`, `position`, `evidence_ids`.
- `trace`: `structure_id`, `text`, `evidence_ids`.
- `overview`: `what`, `how`, `invariant`, `evidence_ids`.

`size.unit` is `loc`, `files`, or `role`. Structure `kind` is `service`,
`module`, `worker`, `library`, `store`, or `role`. Edge `kind` is `flow`,
`advisory`, or `build`.

Each structure's `talks` array is a compact dependency index and must exactly
equal the unique target IDs of its outgoing `edges` records. The interactive
detail derives relationship labels and kinds from `edges`, which remain the
authoritative relationship source.

Use safe IDs (`[A-Za-z0-9][A-Za-z0-9._-]*`) up to 64 characters. Text is
bounded by the validator so the navigation and durable artifact remain compact.
