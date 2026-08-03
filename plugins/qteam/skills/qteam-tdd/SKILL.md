---
name: qteam-tdd
description: Enforce behavior-first RED/GREEN development for QTeam features and bug fixes. Use before production edits when a task changes observable behavior, needs a regression test, or declares TDD evidence and approved test seams.
---

# QTeam TDD

Read `CONTEXT.md` and relevant ADRs when present, plus every approved
`test_seams` entry and the complete `scenario_coverage` matrix. Each
entry freezes a seam ID, observable behavior, exact non-glob `test_paths`, focused command,
and expected RED pattern. Treat that contract as fixed; stop if the behavior
cannot be reached there instead of inventing a production API for the test.
Read [scenario-coverage.md](references/scenario-coverage.md). Every dimension
must have one strongest applicable scenario linked to a seam or an explicit
non-applicability rationale; equivalent scenarios do not become duplicate
tests.

For each thin vertical slice:

1. Name the observable break the test catches and the production mutation that
   should make it fail. Derive expected values independently from literals,
   worked examples, or the spec—not from production helpers.
2. Add one behavior test through one approved public seam. Change only that
   seam's declared `test_paths`; do not write all tests horizontally before
   implementation.
3. Run the focused command. Confirm a real assertion failure for the expected
   missing behavior, not a syntax/setup error or an unrelated failure. Commit
   this test-only state as the RED commit. It must immediately follow task base
   (first seam) or the preceding seam's GREEN commit.
4. Add the smallest coherent production change. Do not anticipate later tests,
   add fallback behavior, or refactor adjacent code. Commit the GREEN state
   immediately after RED; defer local cleanup until every seam cycle is
   recorded.
5. Ask the coordinator to run `.codex/bin/agent-team-state --run <run>
   verify-tdd-cycle <task> --seam <id> --red-commit <red-sha> --green-commit
   <green-sha>`. QTeam replays the pre-approved command and
   failure pattern at both commits; neither worker nor coordinator substitutes
   evidence after implementation. Every planned seam must be verified.
6. Run the focused test, then the task record's `verification` command. Keep
   output free of warnings and repeat for the next slice.

Use real components by default. Mock only slow or external system boundaries,
preserve every real side effect the test depends on, and mirror complete real
response shapes. Never assert that a mock exists or that an internal method was
called unless that interaction is itself the public contract.

After every approved seam is GREEN, allow only production-only local cleanup
such as names or immediate duplication in a later commit, then rerun all
focused commands. Never edit a proven test file after its GREEN commit; test
cleanup requires a newly approved seam cycle. Put
structural refactoring in a separate `refactor` task and
keep its characterization suite GREEN throughout; do not manufacture a RED
failure for behavior-preserving work. Generated code, human prose, and
configuration skip TDD only through the corresponding non-TDD `work_kind`, not
through an informal exception.

Read [test-quality.md](references/test-quality.md) when choosing assertions,
mocks, fixtures, or mutation checks.
