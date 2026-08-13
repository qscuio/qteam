# Quality lane evidence

## Refactor

Use after behavior is green. Inspect duplication, ownership, dependency
direction, error flow, and overly coupled seams changed by this wave. Make only
behavior-preserving improvements inside an owned task. Freeze commands that run
the focused behavior suite and a repository-native structural/static check.

Good evidence: focused tests plus lint/typecheck/complexity rule that covers the
changed module. Weak evidence: formatting alone, a prose cleanup claim, or a
repository-wide redesign.

## Hardening

Choose the adversary from the named risk:

- state/data logic: mutation or property/generative tests;
- concurrency: stress/race/deadlock/ordering test;
- security/auth: negative authorization and boundary/fuzz cases;
- migration/data loss: rollback, replay, interruption, and partial-write tests;
- external integration: injected timeout, malformed response, and retry/idempotency.

Freeze a deterministic seed or a bounded attempt count. Preserve a failing
counterexample as a regression case before calling the lane passed.

## Public-surface QA

Test from outside the implementation boundary: CLI invocation, HTTP/client
contract, package import, generated schema, help/example flow, or documented
upgrade path. Cover one successful consumer flow, one invalid-input flow, and
the compatibility promise named by policy. The command must use the installed
or built public artifact where practical, not private helper calls alone.
