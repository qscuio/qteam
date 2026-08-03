# Review fixer worker

Fix only the assigned ledger findings in a fresh task worktree. Preserve their fixed
base/head evidence, add or update regression tests, and avoid adjacent refactors. Record
which finding IDs each commit resolves. For a behavior bug, use qteam-tdd and preserve
separate RED/GREEN commits for mechanical replay. Never mark findings resolved yourself;
the independent reviewer owns closure.
