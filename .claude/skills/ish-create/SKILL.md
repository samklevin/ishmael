---
name: ish-create
description: Use when the user wants to create a task/bead for ishmael agents to work on
disable-model-invocation: true
---

# Create a Bead

Create a bead (task) that the ishmael orchestrator will assign to a Claude Code agent worker.

## Steps

1. **Parse arguments**: The user may provide a title and/or description inline (e.g. `/ish:create "Fix the login bug"`). If not provided, ask for a title.

2. **Determine repo**: Check the current working directory for a `.git` directory. Use the absolute path to the repo root. If not in a git repo, ask the user which repo to target.

3. **Determine branch**: Default to `main`. If the user specifies a branch, use that instead.

4. **Write an agent-oriented description**: The description should be written as instructions for an autonomous Claude Code agent. It should:
   - State the objective clearly in the first sentence
   - Include specific files or areas of the codebase to look at (if known)
   - Define what "done" looks like (acceptance criteria)
   - Be self-contained — the agent won't have conversation context

   If the user gave a short description, expand it into agent-oriented instructions. Ask clarifying questions if the task is ambiguous.

5. **Set priority**: Default to 2 (medium). If the user specifies urgency, map it: low=1, medium=2, high=3, critical=4.

6. **Create the bead**: Call the `create_bead` MCP tool with:
   - `title`: short task name
   - `repo`: absolute path to the git repository
   - `branch`: branch name
   - `description`: agent-oriented instructions
   - `priority`: 0-4
   - `worktree` (optional): path to an existing worktree to reuse. Use this when
     creating a bead that should share a worktree with another bead (e.g. dependent
     tasks in a chain). If not provided, a fresh worktree is created.

7. **Report**: Show the user the bead ID, worktree path, and a summary of what was created.
