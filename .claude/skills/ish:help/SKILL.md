---
name: ish:help
description: Use when the user wants an overview of ishmael, its commands, tools, and skills
disable-model-invocation: true
---

# Ishmael Help

Give the user a quick overview of ishmael and point them to the right skill or command.

## Steps

1. **Show this overview**:

   Ishmael is a tmux-based orchestrator that assigns beads (tasks) to Claude Code agent workers running in parallel. Each agent gets its own git worktree.

   **Bead lifecycle**: open -> in_progress -> completed/failed (failed beads can be retried)

2. **Show available skills**:

   | Skill | What it does |
   |-------|-------------|
   | `/ish:create` | Create a single bead for agents to work on |
   | `/ish:plan` | Break down a goal into tasks (planning only, no beads created) |
   | `/ish:dispatch` | Convert a plan into a chain of beads with dependencies |
   | `/ish:status` | Show all beads and running agents |
   | `/ish:board` | Show running agents and how to jump into their sessions |
   | `/ish:retry` | Retry a failed bead |
   | `/ish:templates` | Learn about, create, or manage workflow templates |
   | `/ish:dispatch-workflow` | List and run a workflow template to create bead chains |
   | `/ish:setup` | Set up ishmael on a new machine |
   | `/ish:help` | This help overview |

3. **Show CLI commands**:

   ```
   ishmael run                          # Start orchestrator (tmux session)
   ishmael add "Title" --repo .         # Create a bead
   ishmael board <bead-id>              # Jump into agent session
   ishmael status                       # Show bead status
   ishmael workflow list                # List workflow templates
   ishmael workflow run <name> --repo . # Run a workflow template
   ishmael setup                        # Install skills + MCP server
   ```

4. **Show MCP tools** (available when `ishmael-mcp` is configured):

   `create_bead`, `get_bead`, `list_beads`, `update_bead`, `retry_bead`,
   `list_active_agents`, `add_dependency`, `remove_dependency`, `list_dependencies`,
   `list_templates`, `instantiate_workflow`

5. **Point to docs**: For architecture details, see `CLAUDE.md` in the ishmael repo.
