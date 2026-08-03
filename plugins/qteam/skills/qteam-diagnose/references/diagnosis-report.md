# Diagnosis Report Contract

Write `.qteam-diagnosis.json` as one JSON object:

```json
{
  "schema_version": 1,
  "repro_commit": "full commit SHA containing the deterministic RED repro",
  "feedback_loop": "exact unattended command",
  "observed_red": "exact symptom and decisive output",
  "minimized_repro": "smallest load-bearing scenario",
  "hypotheses": [
    {
      "rank": 1,
      "statement": "candidate cause",
      "prediction": "observable result if true",
      "check": "one-variable probe performed",
      "outcome": "evidence that confirmed or falsified it"
    }
  ],
  "root_cause": "original trigger, not immediate symptom",
  "causal_chain": ["origin", "boundary", "symptom"],
  "fix_boundary": "owned source location and minimal fix scope",
  "cleanup": "planned proof: original loop GREEN, regression GREEN, markers/harness removed",
  "preventive_lesson": "specific design/test/observability rule that prevents recurrence"
}
```

Provide three to five hypotheses with consecutive ranks. `feedback_loop` must
exactly equal the task's approved `diagnosis_command`; the repro output must
contain its `failure_pattern`. Do not include secrets, raw private logs, or
unbounded command output; point to durable run logs when needed.
