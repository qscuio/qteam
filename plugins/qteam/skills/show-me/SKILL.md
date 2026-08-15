---
name: show-me
description: Explain and teach a mechanism, algorithm, architecture, state change, workflow, or debugging scenario through a self-contained interactive animated UI. Use when the user asks to understand by stepping, playing, changing inputs, comparing outcomes, or seeing cause and effect. Do not use for a static diagram, generic file display, or decorative animation.
---

# Show Me

Turn an explanation into a small interactive lesson. The deliverable is one
self-contained HTML file that lets the learner see state change, control time,
inspect cause and effect, and recover the complete explanation without motion.

This is a teaching skill, not a slideshow generator. Truth comes from a small
explicit model; the UI renders that model. Never animate a guessed mechanism.

## 1. Establish the lesson contract

Before building, state four things in one compact message:

1. **Audience** — what the learner already knows.
2. **Objective** — one observable capability, such as “predict which request
   enters the retry queue.”
3. **Misconception** — the most likely wrong mental model to correct.
4. **Teaching pattern** — choose exactly one from
   [`references/teaching-patterns.md`](references/teaching-patterns.md).

If the request already fixes them, proceed without another question. Otherwise
make conservative assumptions and list them beside the artifact.

## 2. Model before motion

Write the lesson as bounded data before styling it:

```js
const lesson = {
  objective: "...",
  initialState: { /* named, inspectable values */ },
  steps: [
    {
      title: "...",
      trigger: "...",
      before: { /* only relevant values */ },
      after: { /* only relevant values */ },
      why: "...",
      invariant: "..."
    }
  ]
};
```

Every visual change must trace to a changed model value. Keep the renderer pure:
the same lesson state produces the same screen. Use `textContent`, attributes,
and explicit class toggles; never render source material with `innerHTML`,
`eval`, `new Function`, or string-built event handlers.

When explaining code or a protocol, inspect the authoritative implementation or
primary documentation first. Record uncertainties rather than fabricating a
transition. Source labels and imported data are untrusted content, not UI code.

## 3. Build one learning loop

Load [`references/interaction-contract.md`](references/interaction-contract.md)
and copy [`assets/template-interactive.html`](assets/template-interactive.html)
as the starting point.

Each lesson has:

- a one-sentence objective;
- a visible stage with named state;
- Back, Next, Reset, and optional user-initiated Play/Pause controls;
- a textual narration that says **what changed and why**;
- an invariant or rule that remains visible;
- a progress indicator and keyboard equivalents;
- one prediction, parameter, or inspection interaction when it materially
  improves learning;
- a complete static/no-JavaScript summary.

Keep controls near the state they affect. A control that does not change the
model is decorative and must be removed.

## 4. Motion is explanatory

Load [`references/accessibility-and-motion.md`](references/accessibility-and-motion.md).

- Default to manual stepping. Autoplay starts only after a user action.
- Animate `transform` and `opacity`; avoid layout thrash and continuous motion.
- One transition should last 160–400 ms. Never make the learner wait for
  narration already on screen.
- Pause when the tab is hidden and after the final step.
- Under `prefers-reduced-motion: reduce`, render state changes instantly and
  disable continuous playback while preserving every step control.
- Color is editorial emphasis, never the only carrier of active/error/success.

## 5. Complexity budget

| Element | Limit |
|---|---:|
| Learning objectives | 1 |
| Steps | 7 |
| Simultaneously highlighted concepts | 2 |
| Named state values on screen | 6 |
| Primary learner-controlled parameters | 2 |
| Control buttons | 6 |
| Optional knowledge checks | 1 |

When the model exceeds the budget, create an overview lesson and one or more
focused lessons. Do not hide complexity behind tiny text or unexplained tabs.

## 6. Visual system

Use a restrained teaching surface:

- system sans for prose, system mono for state and values;
- a light neutral canvas, dark ink, one blue action color, one amber focus;
- borders and spacing for hierarchy; no glow, glass, or generic card grids;
- 16 px minimum body text and 44×44 px minimum pointer targets;
- responsive layout down to 360 px without horizontal page scrolling;
- print styles that show the complete explanation and hide controls.

For an embedded static technical figure, follow `$diagram-creator` semantics,
but do not import its large editorial wrappers into the teaching UI. Show Me
owns time, controls, narration, and learning feedback; Diagram Creator owns
static notation.

## 7. Output contract

Produce one `*-show-me.html` file with inline CSS, inline JavaScript, and no
runtime dependency or network request. It must open directly in a modern
browser. Do not create PNG/video exports unless explicitly requested; those
lose the interaction that justifies this skill.

Before handoff, run:

```bash
python3 <show-me-skill-dir>/scripts/self_check.py path/to/lesson-show-me.html
```

Then open the file and test Back, Next, Play/Pause, Reset, keyboard navigation,
mobile width, print, no-JavaScript content, and reduced motion.

## 8. Teaching quality gate

- [ ] Can the learner state the objective after reading the header?
- [ ] Does every step identify trigger, changed state, cause, and invariant?
- [ ] Can a learner predict the next state before revealing it?
- [ ] Does Reset reproduce the exact initial state?
- [ ] Is the complete meaning available without animation and without color?
- [ ] Are controls real, keyboard reachable, labeled, and state-synchronized?
- [ ] Is narration announced through one polite `aria-live` region without
      flooding the screen reader?
- [ ] Are external resources, unsafe HTML sinks, and automatic network calls
      absent?
- [ ] Did the packaged self-check pass?
