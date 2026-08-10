# Third-party notices

The `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling`, `to-spec`,
`to-tickets`, `wayfinder`, `qteam-tdd`, `qteam-diagnose`, and their references
adapt workflow ideas from Matt Pocock's skills collection (MIT License,
copyright 2026 Matt Pocock). They have been rewritten to make QTeam the sole
orchestration authority. The TDD synthesis retains public behavior seams,
vertical RED/GREEN slices, independent expected values, and boundary-only
mocking; diagnosis retains feedback-loop-first, minimized-repro, and ranked
falsifiable-hypothesis practices.

`qteam-tdd` and `qteam-diagnose` also adapt RED-before-production, verified
failure, minimal GREEN, root-cause tracing, and cleanup principles from
Superpowers (MIT License, copyright 2025 Jesse Vincent). The original source
snapshot is archived under `upstream/superpowers/`, outside the plugin's
discoverable `skills/` directory. Only the bounded, QTeam-owned primitives in
`skills/` are exposed. The upstream license is reproduced in
`LICENSES/Superpowers-MIT.txt`.

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
