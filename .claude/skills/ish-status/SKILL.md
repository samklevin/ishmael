---
name: ish-status
description: Use when the user wants to see the status of ishmael beads or running agents
disable-model-invocation: true
---

# Show Ishmael Status

Show an overview of all beads and running agents.

## Steps

1. **Fetch data**: Call both MCP tools in parallel:
   - `list_beads` (no filters — returns all open beads)
   - `list_active_agents`

2. **Also fetch completed**: Call `list_beads` with `status: "closed"` to get recently completed beads.

3. **Format output** into these sections, skipping empty sections:

   **Running** — Beads currently being worked on by agents. Show: bead ID, title, assignee, elapsed time.

   **Ready** — Open beads with no blockers, waiting for an agent. Show: bead ID, title, priority.

   **Blocked** — Beads waiting on dependencies. Show: bead ID, title, what they're blocked by.

   **Recently Completed** — Last few closed beads. Show: bead ID, title.

4. **Output**: Present as a clean, readable summary. This is a one-shot overview — no follow-up actions needed.
