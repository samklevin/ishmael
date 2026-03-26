# Ishmael — Agent Orchestrator

Ishmael is a tmux-based orchestrator that assigns **beads** (tasks from the `bd` issue tracker) to Claude Code agent workers running in parallel. Each agent gets its own tmux window and git worktree.

## Architecture

```
ishmael run  (tmux session "ishmael")
├── Window 0: Dashboard (3 panes)
│   ├── Left:         Bead list (auto-refreshing)
│   ├── Right-top:    Agent status + elapsed time
│   └── Right-bottom: Orchestrator log
├── Window 1: Agent for bead abc-123
├── Window 2: Agent for bead def-456
└── ...
```

The orchestrator polls `bd ready` every 5 seconds. When a bead is ready, it:
1. Claims it via `bd update <id> --claim`
2. Creates a git worktree (isolated branch)
3. Spawns a Claude Code worker in a new tmux window
4. Monitors the worker — closes the bead on success, resets on failure

## Key Commands

```bash
ishmael run                    # Start orchestrator (creates tmux session)
ishmael add "Title" --repo .   # Create a bead for a repo
ishmael board <bead-id>        # Jump to agent window or resume in worktree
ishmael status                 # Show bd status
ishmael workflow list          # List workflow templates
ishmael workflow run <name> --repo . --param key=value
```

## MCP Tools (available to Claude Code instances)

When `ishmael-mcp` is configured as an MCP server, Claude Code gets these tools:

| Tool | Purpose |
|------|---------|
| `create_bead` | Create a task with repo, branch, description, priority, and dependencies |
| `get_bead` | Get full details of a bead by ID |
| `list_beads` | List beads with optional status/repo/assignee filters |
| `update_bead` | Update description, priority, status, labels, notes, or metadata |
| `retry_bead` | Close a failed bead and recreate it fresh |
| `list_active_agents` | Show which beads have running agents |
| `add_dependency` | Make one bead block another |
| `remove_dependency` | Remove a blocking relationship |
| `list_dependencies` | Show what blocks a bead |
| `list_templates` | List available workflow templates |
| `instantiate_workflow` | Create a chain of beads from a template |

### Creating work for agents

To create a task that the orchestrator will pick up and assign to an agent:

```
Use create_bead with:
  - title: short task name
  - repo: absolute path to the git repository
  - branch: branch to base the worktree on (e.g. "main")
  - description: detailed instructions for the agent
  - priority: 0-4 (higher = picked up sooner)
  - blocked_by: comma-separated bead IDs (optional)
```

The orchestrator automatically assigns ready beads to agents. You do not need to manually start agents.

### Workflow templates

Templates define chains of beads with dependencies (e.g. design -> implement -> review -> validate). They live in `~/.ishmael/templates/*.yaml`. Use `list_templates` to see available ones, then `instantiate_workflow` to create the full chain.

## Bead Lifecycle

```
open → [orchestrator claims] → in_progress → completed/failed
                                    ↑              ↓
                                    └── retry ──────┘
```

- **open**: Available for assignment
- **blocked**: Has unresolved dependencies (waiting for other beads to close)
- **in_progress**: Claimed by an agent, worker running
- **completed**: Agent finished successfully, bead closed
- **failed**: Agent crashed or errored — can be retried

## File Layout

```
ishmael/
├── __main__.py            # CLI entry point (ishmael run/add/board/status/workflow)
├── _orchestrator_main.py  # Runs inside tmux window 0 (dashboard + poll loop)
├── orchestrator.py        # Core orchestrator logic (poll, assign, monitor)
├── agent.py               # Agent lifecycle (spawn, poll, kill, reconnect)
├── worker.py              # Detached worker process (runs claude CLI for one bead)
├── tmux.py                # Stateless tmux CLI wrapper
├── worktree.py            # Git worktree creation/cleanup
├── config.py              # Configuration dataclass
├── templates.py           # Workflow template loading and instantiation
└── mcp_server.py          # MCP server exposing ishmael tools to Claude Code
```

## Non-Interactive Shell Commands

Agents run in non-interactive environments. Always use `-f` flags:
```bash
cp -f source dest     # NOT: cp source dest
mv -f source dest     # NOT: mv source dest
rm -f file            # NOT: rm file
```
