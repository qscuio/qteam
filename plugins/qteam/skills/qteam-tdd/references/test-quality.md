# Test Quality Gates

Use these gates before keeping a test:

- Test caller-visible behavior through the highest stable approved seam.
- Name the realistic production break it detects. If only source text or an
  intentional constant can make it fail, replace it with a behavior test.
- Derive expected results independently. Prefer hand-checked literals and
  fixtures; never compute both sides with the same algorithm or helper.
- Keep one logical behavior per cycle. Split names containing unrelated “and”
  clauses.
- Exercise real code. Mock external APIs, time, randomness, or an expensive
  boundary only when necessary; prefer a real test database or component when
  practical.
- Learn the real dependency's side effects before mocking below it. Keep needed
  state changes real and make doubles return complete production-shaped data.
- Put test-only setup and cleanup in test utilities, never in production APIs.
- Prefer a real integration test when mock setup exceeds the behavior under
  test or must branch on many internal details.

Before completion, mentally mutate the implementation: wrong branch, missing
side effect, empty return, wrong argument, or missing zero/empty/unauthorized
case. At least one retained test must fail for every realistic mutation the
task claims to protect.
