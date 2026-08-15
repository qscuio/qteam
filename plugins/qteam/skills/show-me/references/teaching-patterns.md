# Teaching patterns

Choose one dominant pattern. Combining patterns usually produces a dashboard
that demonstrates many things and teaches none.

## Guided walkthrough

Use when order and causality matter. Back/Next reveals one transition at a
time. Before revealing, let the learner predict the next active node or value.
Best for request paths, protocol exchanges, build pipelines, and algorithms.

## State explorer

Use when a small state machine has meaningful branches. The learner chooses a
legal event; the UI shows the resulting state and explains why illegal events
are rejected. Best for lifecycle, authorization, retries, and concurrency.

## Parameter lab

Use when one or two inputs change a deterministic output. Sliders/selects show
the result and the invariant. Include at least one boundary value. Best for
capacity, backoff, scheduling, layout, and performance tradeoffs.

## Side-by-side comparison

Use when the learning objective is a distinction. Keep inputs synchronized and
highlight the first divergence. Best for algorithms, policies, old/new design,
or correct/incorrect mental models.

## Timeline replay

Use when evidence already has timestamps or an ordered event log. Scrubbing or
stepping reveals observed events; it must not invent missing events. Best for
debugging, distributed traces, incidents, and history.

## Selection test

| Question | Pattern |
|---|---|
| “What happens next, and why?” | Guided walkthrough |
| “What events are legal from here?” | State explorer |
| “What changes if I alter X?” | Parameter lab |
| “Why do these approaches differ?” | Side-by-side comparison |
| “What actually happened over time?” | Timeline replay |
