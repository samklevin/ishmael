---
name: ish:plan
description: Use when the user wants to break down a high-level goal into tasks for ishmael agents
disable-model-invocation: true
---

# Plan Work for Ishmael Agents

Break down a high-level goal into a structured plan of tasks with dependencies. This is a planning step only — no beads are created.

## Steps

1. **Get the goal**: The user provides a high-level goal as an argument (e.g. `/ish:plan Add user authentication`). If no goal is provided, ask for one.

2. **Explore the codebase**: Use Glob, Grep, and Read tools to understand:
   - The project structure and key files
   - Existing patterns and conventions
   - What already exists vs what needs to be built
   - Potential dependencies between pieces of work

3. **Break down into tasks**: Decompose the goal into discrete tasks that can each be completed by an independent agent. Each task should:
   - Be completable in a single agent session
   - Have clear boundaries and acceptance criteria
   - Not be too large (prefer smaller, focused tasks)
   - Not be too small (don't create a bead for trivial one-line changes)

4. **Identify dependencies**: Determine which tasks must complete before others can start. Common patterns:
   - Schema/type changes before implementation
   - Core utilities before features that use them
   - Implementation before tests (or vice versa for TDD)
   - Multiple independent tasks can run in parallel

5. **Present the plan**: Output a numbered list of tasks with:
   - Title
   - Brief description of what the agent should do
   - Dependencies (which tasks must complete first)
   - Estimated complexity (small / medium / large)

   Use a visual format that shows the dependency graph clearly.

6. **Do NOT create beads**. Tell the user: "Run `/ish:dispatch` when you're ready to create these as beads."
