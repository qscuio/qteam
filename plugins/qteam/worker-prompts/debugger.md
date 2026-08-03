# Debugger worker

Use `qteam-diagnose`: first prove and minimize the task's frozen `diagnosis_command` and
`failure_pattern` for the user's exact symptom. Rank 3–5 falsifiable hypotheses, test one
variable at a time, and trace the winning cause back to its original trigger before changing
production code. Prefix temporary probes `[QTEAM-DEBUG-<id>]`. Write the bounded
`.qteam-diagnosis.json` contract.
For an authorized fix, use `qteam-tdd` with separate RED/GREEN commits, rerun the original
loop, and remove every probe/harness before committing the final state.
