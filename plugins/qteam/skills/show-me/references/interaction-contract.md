# Interaction contract

## State authority

Keep one immutable lesson definition and one small mutable UI state:

```js
const ui = { step: 0, playing: false, speed: 1 };
```

All rendering derives from those values. Controls update state, then call one
`render()` function. Do not let DOM classes become the source of truth.

## Required controls

- **Back** and **Next** move exactly one step and disable at their boundaries.
- **Reset** returns to the exact initial step and pauses playback.
- **Play/Pause** is optional; if present, its visible label and
  `aria-pressed`/state reflect reality.
- A progress bar and text announce `Step N of M`.
- `ArrowLeft`, `ArrowRight`, `Home`, and `End` mirror the buttons. Space toggles
  playback only when focus is not in an input, select, textarea, or button.

Focus stays on the activated control. Do not move focus on every step. Put the
changing narration in one `aria-live="polite"` region and update it once per
transition.

## Learner interaction

A prediction should be answerable before the result appears. A parameter lab
must label current values and include boundary behavior. A state explorer must
disable or explain illegal events. Never score a learner without also
explaining the governing rule.

## Lifecycle

- Pause when `document.visibilityState === "hidden"`.
- Pause at the last step.
- Clear pending timers before starting a new one.
- Respect reduced motion at startup and when its media query changes.
- Preserve the complete final/static explanation in `<noscript>` and print.
