---
name: ish-retry
description: Use when the user wants to retry a failed ishmael bead/task
disable-model-invocation: true
---

# Retry a Failed Bead

Show failed beads and retry the one the user selects.

## Steps

1. **Find failed beads**: Call `list_beads` with status filter to find beads that have failed or errored. Check for beads with status "closed" that may have failed, and also "open" beads that were previously attempted.

2. **Present options**: If there are multiple failed beads, show them as a numbered list with:
   - Bead ID
   - Title
   - Brief description snippet

   If the user provided a bead ID as an argument (e.g. `/ish:retry abc-123`), skip to step 3.

   If there are no failed beads, tell the user "No failed beads found" and suggest `/ish:status` to see all beads.

3. **Confirm and retry**: Let the user pick which bead to retry (or confirm the one specified). Call `retry_bead` with the bead ID.

4. **Report**: Show the new bead ID and confirm it's been queued for the orchestrator to pick up.
