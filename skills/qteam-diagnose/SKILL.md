---
name: qteam-diagnose
description: Unified evidence-first diagnosis workflow for application, frontend, and system failures.
---

# QTeam Diagnose

1. Capture the exact observed and expected behavior and the smallest fast,
   deterministic reproduction.
2. Inspect recent changes and a known working analogue. Trace the input/data
   path backward from the symptom.
3. Write 3–5 falsifiable hypotheses, ordered by evidence. Run the smallest
   discriminating check for each; do not patch while guessing.
4. State the proven root cause and ownership boundary. If three attempts fail
   on the same cause, question the architecture and return to QTeam replanning.
5. When a fix is authorized, create a fresh fix task, prove RED with a
   regression test, apply the minimal root-cause fix, prove GREEN, and pass the
   normal mechanical/review gates.

Role prompts select domain tools; they do not duplicate this workflow.
