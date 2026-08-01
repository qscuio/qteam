---
name: qteam-tdd
description: Unified behavior-first TDD for QTeam writable workers.
---

# QTeam TDD

Read the approved test seam from the task record. For one thin vertical
behavior slice:

1. Add a test at the highest stable public seam already used by the project.
2. Run it and preserve RED evidence. It must fail because the behavior is
   absent, not because the test is broken.
3. Implement the smallest coherent behavior without fallback or workaround.
4. Run the focused command and preserve GREEN evidence.
5. Refactor only while behavior remains green, then continue to the next slice.

Every new externally observable behavior needs coverage. Internal helpers do
not each require their own test. Do not create production APIs solely to expose
internals unless the approved spec selected that seam.
