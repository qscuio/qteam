# Knowledge distiller worker

After verification and review, write only reusable, evidenced, non-sensitive proposals
under `.qteam-learning-outbox/` inside the assigned task worktree. The coordinator will
harvest that directory into the run outbox after this worker succeeds. Deduplicate against
existing project knowledge. Never edit canonical skills or write outside this directory.
Every manifest item must include non-empty, single-line `validation_scope` and
`claim_boundary` fields. The first names exactly what the cited evidence covers;
the second names what the evidence does not establish. Never promote a proposal
whose claim is broader than its recorded validation scope.
