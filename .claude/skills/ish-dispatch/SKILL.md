---
name: ish-dispatch
description: Use when the user wants to dispatch/execute a plan by creating beads with dependencies for ishmael agents
disable-model-invocation: true
---

# Dispatch Plan as Beads

Convert a plan from the current conversation into a chain of beads with dependencies that ishmael agents will execute.

## Steps

1. **Read conversation context**: Look back through the conversation for a plan that was discussed. This is typically the output of `/ish:plan` or a discussion about breaking down work into tasks. If no plan is found, tell the user to describe or plan the work first (suggest `/ish:plan`).

2. **Determine repo and branch**: Check the current working directory for a `.git` directory. Use the absolute path to the repo root and default branch to `main`. If not obvious from context, ask the user.

3. **Extract tasks**: From the plan, identify:
   - Individual tasks with clear boundaries
   - The dependency order (which tasks must complete before others can start)
   - A short title for each task
   - A detailed, agent-oriented description for each task

4. **Write agent-oriented descriptions**: Each description must be self-contained instructions for an autonomous agent. Include:
   - Clear objective in the first sentence
   - Specific files or areas to modify
   - Acceptance criteria (what "done" looks like)
   - Any constraints or patterns to follow
   - The agent has NO conversation context — the description must stand alone

5. **Create the first bead**: Call `create_bead` with title, repo, branch, description, and priority. Note the returned bead ID **and worktree path** from the response.

6. **Create remaining beads sharing the same worktree**: For all subsequent beads in the chain, pass the `worktree` parameter set to the worktree path from step 5. This ensures all beads in the chain work on the same code, so later beads see changes made by earlier ones.

   For each bead:
   - Call `create_bead` with title, repo, branch, description, priority, **and `worktree`**
   - Pass `blocked_by` with comma-separated IDs of beads this task depends on
   - Note the returned bead ID

7. **Report**: Show the full chain with:
   - Each bead's ID, title, and what it's blocked by
   - The shared worktree path
   - A visual dependency graph (simple text format)
   - Total number of beads created
