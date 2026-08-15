---
name: isometric
description: >-
  Create or update a self-contained interactive isometric codebase map backed
  by measured repository facts and source digests. Use for whole-repository
  architecture orientation, subsystem maps, request traces, or an isometric
  "architecture city". Do not use for one mechanism or UML view
  ($diagram-creator), an interactive lesson ($show-me), or product UI mockups.
---

# Isometric Codebase Map

Create one stable, evidence-backed HTML artifact that explains a repository as
an interactive isometric map. Facts determine the map; the desired visual must never determine
the facts.

## Authority and boundaries

- This is an artifact skill, not an orchestrator. In an active QTeam run, keep
  the artifact inside its task, write-set, review, and finish gates.
- Use existing `researcher` and `architect` roles when a run owns the work. Do
  not create a permanent map role or automatically spawn a fixed subagent.
- Use parallel research only when repository areas have independent source and
  output boundaries. Merge findings through one evidence ledger.
- Route a single architecture/UML/mechanism drawing to `$diagram-creator` and a
  learner-controlled explanation to `$show-me`.

## Output contract

- Start from `assets/template.html`; edit only the JSON inside
  `<script id="isometric-data" type="application/json">` and the marked
  `#isometric-static-summary` fallback whose text must mirror that JSON.
- Keep the engine script byte-equivalent to the packaged template.
- Produce one offline HTML file: no sidecars, remote resources, dynamic network
  APIs, arbitrary scripts, or remote fonts.
- Every statistic, structure, connection, external boundary, trace step, and
  overview claim references evidence whose repository-relative source files are
  bound by SHA-256.
- Use as many structures as the repository honestly supports, from 1 to 40.
  Never invent modules to meet a visual quota.
- Preserve stable structure IDs, coordinates, and the output filename on an
  update unless the architecture itself changed.

Read [references/evidence-contract.md](references/evidence-contract.md) before
building the data packet. Read
[references/visual-contract.md](references/visual-contract.md) before choosing
coordinates or reviewing screenshots.

## Workflow

### 1. Establish repository identity

Record the source snapshot Git HEAD and whether the worktree is dirty after
excluding the output map itself. Define the scan scope and exclusions.
Generated output, vendored dependencies, caches, build artifacts, and lockfiles
are excluded unless they are architecturally material. Generate from a source
commit, then commit an in-repository map separately; validation permits commits
after the source snapshot only when they change that exact map path.

### 2. Build one evidence ledger

Inspect manifests, entry points, dependency declarations, route registration,
storage adapters, queue/event producers and consumers, deployment files, and
tests. Prefer `scc` or `cloc`; otherwise use reproducible `rg`/`wc` commands.

For every evidence item, record:

- a stable evidence ID and one bounded claim;
- canonical repository-relative source paths plus their SHA-256 values;
- the exact measurement command, result, and exclusions when the claim is a
  count or size.

Reconcile the evidence into viewer-level structures. Merge helpers into their
owner. Distinguish deployed services from code-level roles and libraries.

### 3. Populate only the data script

Copy the template to the stable output path. Replace the sample JSON with a
schema-version-1 packet and set `sample_data` to `false`. Update the marked
static summary with the same title, invariant, and complete structure list;
change nothing else. Keep explanations in plain language under `what`, and
concrete code organization under `how`.

Include a trace only when the repository supports an honest runtime, command,
build, or data path. A trace has 2-20 steps; it is not padded to a quota.

### 4. Lay out from measured facts

- Keep grid footprints disjoint; the validator rejects overlap.
- Put entry surfaces toward the top, core responsibilities centrally, compute
  to the sides, and stores toward the bottom.
- Scale height consistently from measured size with a documented clamp when
  one structure would dominate.
- Use `flow` edges for runtime/data movement, `advisory` for control or metadata,
  and `build` for delivery relationships.
- Change coordinates to improve legibility; never change evidence to improve
  composition.

### 5. Verify mechanically and visually

Run the repository-bound validator before publishing:

```bash
python3 <skill-dir>/scripts/validate_isometric.py \
  /absolute/path/to/map.html --repo /absolute/path/to/repository
```

Repeat `--forbid-prefix PREFIX` for organization or infrastructure prefixes
that must not be published. The validator checks the data contract, evidence
digests, references, footprint overlap, closed CSP, single-file resources, and
the packaged engine identity.

Then open these deterministic states in an available browser surface:

- default: `map.html`
- one structure: `map.html#structure=<id>`
- one drill-down, when present: `map.html#inside=<id>`
- a middle trace step, when present: `map.html#trace=<n>`

Inspect at a wide desktop viewport and a narrow viewport. Check the console,
labels, edges, keyboard focus, detail panel, reduced motion, no-JavaScript
summary, and print view. Do not download an unpinned browser package merely to
run this check; use an installed browser/tool or report the unavailable visual
check honestly.

### 6. Perform the sharing pass

Remove account/project IDs, concrete cloud resources, internal endpoints,
credential names or formats, mount paths, email addresses, and private handles.
Use generic boundary labels when operational specificity is unnecessary. Never
rewrite repository source during this pass. Run the validator again after every
redaction.

## Updating a map

Rescan changed areas and their dependency neighbors. Preserve unaffected IDs,
coordinates, evidence IDs, and the filename. Refresh every changed hash,
measurement, description, connection, child, statistic, and trace step; then
repeat the complete mechanical and visual verification.
