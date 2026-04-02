# Ishmael

Agent orchestrator that assigns tasks to Claude Code workers running in parallel tmux windows. Uses [beads (bd)](https://github.com/dougbutner/beads) for task tracking and git worktrees for isolated workspaces.

## Prerequisites

- **Python 3.10+**
- **tmux**
- **git**
- **Claude Code CLI** (`claude`)
- **bd** (beads issue tracker)

### macOS

```bash
brew install tmux git
npm install -g @anthropic-ai/claude-code
```

For Python, install via conda (recommended) or brew:

```bash
brew install miniconda
```

## Install

```bash
# Create conda environment
conda create -n ishmael python=3.10 -y
conda activate ishmael

# Clone and install
git clone <repo-url> ~/Development/ishmael
cd ~/Development/ishmael
pip install -e .
```

## Setup

### 1. Initialize beads database

```bash
bd init
```

### 2. Configure MCP server (optional)

To give Claude Code instances access to ishmael tools, add to `~/.claude.json` or your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "ishmael": {
      "command": "conda",
      "args": ["run", "-n", "ishmael", "--no-banner", "ishmael-mcp"]
    }
  }
}
```

### 3. Set up alias (optional)

Add to `~/.zshrc`:

```bash
alias ish="conda activate ishmael && ishmael run"
```

## Usage

```bash
# Start the orchestrator
ishmael run

# Create a task
ishmael add "Fix login bug" --repo /path/to/project --branch main -d "Description here"

# Jump into an agent's session
ishmael board <bead-id>

# Check status
ishmael status

# Run a workflow template
ishmael workflow list
ishmael workflow run story --repo /path/to/project --param story_id=2-1

# Install skills + MCP server globally
ishmael setup
```

## Slash Commands

After running `ishmael setup`, these skills are available in any Claude Code session:

| Command | What it does |
|---------|-------------|
| `/ish:create` | Create a bead for agents to work on |
| `/ish:plan` | Break down a goal into tasks |
| `/ish:dispatch` | Convert a plan into bead chains |
| `/ish:dispatch-workflow` | Run a workflow template |
| `/ish:status` | Show beads and running agents |
| `/ish:board` | Show running agents |
| `/ish:retry` | Retry a failed bead |
| `/ish:templates` | Manage workflow templates |
| `/ish:setup` | Set up ishmael |
| `/ish:help` | Overview of all commands and tools |

## How It Works

1. `ishmael run` creates a tmux session with a 3-pane dashboard
2. The orchestrator polls for ready beads every 5 seconds
3. When a bead is ready, it creates a git worktree and spawns a Claude Code worker in a new tmux window
4. Workers run autonomously — closing beads on success, adding notes on failure
5. Use `ishmael board <id>` to watch or interact with an agent
