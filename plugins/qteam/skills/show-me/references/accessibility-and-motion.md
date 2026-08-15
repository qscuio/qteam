# Accessibility and motion

## Semantic structure

Use `header`, `main`, named `section` elements, native `button`/`input`
controls, and one clear `h1`. Each visual stage needs an accessible name and a
text equivalent. Decorative SVG is `aria-hidden="true"`; meaningful SVG uses
`role="img"` with `<title>` and `<desc>`.

Do not put interaction on a `div`. Do not encode active, success, or failure by
color alone. Pair it with a label, icon shape, border treatment, or text.

## Reduced motion

Use this baseline:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}
```

The script must also detect the query and disable continuous playback; CSS
alone does not stop timers. Manual Back/Next remains available.

## Motion vocabulary

- A newly active element may move 6–12 px and fade in.
- A changed value may briefly receive a border/fill emphasis.
- A causal edge may reveal from source to destination.
- Never shake errors, pulse indefinitely, use parallax, or autoplay on load.

## Static and print fallbacks

The initial DOM must contain the lesson objective, all step titles or a compact
complete summary, and the governing invariant. Print shows every step in
order, not merely the currently selected state. `<noscript>` repeats the
essential sequence for browsers or policies that disable JavaScript.
