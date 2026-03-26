---
name: ish-board
description: Use when the user wants to see running ishmael agents or jump into an agent's session
disable-model-invocation: true
---

# Show Agent Board

Show running agents and how to jump into their sessions.

## Steps

1. **Fetch active agents**: Call `list_active_agents` MCP tool.

2. **Format output**: For each running agent, show:
   - Bead ID
   - Title
   - Elapsed time
   - Assignee (agent name)

   If no agents are running, say "No agents currently running." and suggest `/ish:status` to see all beads.

3. **Show board command**: Tell the user they can jump into any agent's session with:
   ```
   ishmael board <bead-id>
   ```
   This attaches to the agent's tmux window so they can watch or interact with it.
