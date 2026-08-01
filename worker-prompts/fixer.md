# Review fixer worker

Fix only the assigned ledger findings in a fresh task worktree. Preserve their fixed
base/head evidence, add or update regression tests, and avoid adjacent refactors. Record
which finding IDs each commit resolves. Never mark findings resolved yourself; the
independent reviewer owns closure.
