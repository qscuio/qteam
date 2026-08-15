# Third-party notices

The `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling`, `to-spec`,
`to-tickets`, `wayfinder`, `handoff`, `qteam-tdd`, `qteam-diagnose`, and their references
adapt workflow ideas from Matt Pocock's skills collection (MIT License,
copyright 2026 Matt Pocock). They have been rewritten to make QTeam the sole
orchestration authority. The TDD synthesis retains public behavior seams,
vertical RED/GREEN slices, independent expected values, and boundary-only
mocking; diagnosis retains feedback-loop-first, minimized-repro, ranked
falsifiable-hypothesis practices, and secret-redacted signal capture. QTeam
also adapts Matt's dependency-frontier grilling, one-fresh-context ticket size,
expand/migrate/contract refactors, phase-boundary context choice, primary-source
research artifacts, and deep-module seam vocabulary.

`handoff` additionally adapts Matt Pocock's temporary, secret-redacted,
pointer-not-copy conversation handoff primitive. `wayfinder` tracks the current
upstream map-as-index, named tickets, native dependency frontier, claim,
fog-of-war, and HITL/AFK boundary. QTeam binds both to its durable checkpoint
and typed-handoff authority rather than treating transcript summaries or issue
comments as run state.

`qteam-tdd` and `qteam-diagnose` also adapt RED-before-production, verified
failure, minimal GREEN, root-cause tracing, and cleanup principles from
Superpowers (MIT License, copyright 2025 Jesse Vincent). The original source
snapshot is archived under `upstream/superpowers/`, outside the plugin's
discoverable `skills/` directory. Only the bounded, QTeam-owned primitives in
`skills/` are exposed. The upstream license is reproduced in
`LICENSES/Superpowers-MIT.txt`.

The QTeam coordinator further adapts Superpowers 6.3's spike/bounded/
architectural design routing, durable file handoffs, bounded blocking waits,
and same-shape task grouping. It deliberately does not adopt Superpowers'
independent orchestration loop or its late-round option to park a valid review
finding: QTeam must fix every valid finding before completion.

`qteam-explore` and the QTeam test-design packet adapt bounded metric iteration,
evidence logging, scenario-dimension coverage, saturation, guard checks, and
held-out verification ideas from Udit Goenka's Autoresearch
(MIT License, copyright 2026 Udit Goenka). QTeam uses those ideas only as a
read-only discovery primitive and experiment handoff; it does not expose
Autoresearch as a competing implementation or shipping orchestrator. The
upstream license is reproduced in `LICENSES/Autoresearch-MIT.txt`.

QTeam's scoped human decision gates, typed continuation handoffs, compact
operator packet, evidence-boundary reporting, and public/private publication
check adapt state-interaction ideas from LoopX (MIT License, copyright 2026
LoopX contributors). QTeam implements them inside its existing coordinator and
transactional state manager; it does not import LoopX's token/quota economy or
create a second orchestration kernel. The upstream license is reproduced in
`LICENSES/LoopX-MIT.txt`.

QTeam's deterministic spec preflight, epic-to-spec dependency manifest,
freshness-checked component index, and post-implementation specification drift
proposal adapt workflow ideas from Smart Ralph (MIT License, copyright 2025
tzachbon). QTeam implements them as bounded artifacts and gates inside the
existing coordinator: it does not import Ralph's stop-hook loop, role set,
POC-first test deferral, or state authority. The upstream license is reproduced
in `LICENSES/Smart-Ralph-MIT.txt`.

`diagram-creator` vendors and adapts Diagram Design 2.4.0 by Cathryn Lavery
(MIT License, copyright 2025 Cathryn Lavery), including its editorial
HTML/SVG system, references, examples, templates, and dependency-free import
and self-check scripts. QTeam renames the skill and adds bounded UML class,
use-case, component, deployment, and activity semantics. The upstream license
is reproduced in source as `LICENSES/Diagram-Design-MIT.txt` and in a project
runtime as `.codex/licenses/Diagram-Design-MIT.txt`.

Diagram Creator also redistributes icon material carried by Diagram Design:
Tabler Icons (MIT, https://github.com/tabler/tabler-icons), Simple Icons
(CC0 1.0, https://github.com/simple-icons/simple-icons), log-z/logos (MIT,
https://github.com/log-z/logos), and Devicon (MIT,
https://github.com/devicons/devicon), plus public-domain SAS and
provenance-only Stata/IcePanel marks. Brand
logos remain trademarks of their owners and are included only for
documentation and illustrative use; inclusion implies no endorsement.
The complete Tabler, Simple Icons, log-z/logos, and Devicon license texts ship
in the corresponding source `LICENSES/` files and project `.codex/licenses/`
files.

`isometric` is a clean-room QTeam-native implementation informed by the
evidence-led architecture-city concept in
https://github.com/sayantan94/toolbelt/tree/419388cf0e15d1741d4cfe0fdc9237cd3eef2be5/isometric.
QTeam does not redistribute that project's template, validator, scripts, text,
or other source material. It uses a new bounded JSON contract, offline engine,
repository SHA-256 evidence ledger, and QTeam's existing role/verification
model; no license is asserted for code that was not copied.
