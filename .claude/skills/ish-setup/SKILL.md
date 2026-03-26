---
name: ish-setup
description: Use when the user wants to set up ishmael on a new machine or check their installation
disable-model-invocation: true
---

# Set Up Ishmael

Walk through setting up ishmael on this machine. Check what's installed and guide through anything missing.

## Steps

1. **Check prerequisites**: Run these checks and report status for each:
   - `python3 --version` — need Python 3.10+
   - `git --version` — need git
   - `tmux -V` — need tmux
   - `claude --version` — need Claude Code CLI
   - `bd --version` or `bd status` — need bd (bead tracker)

2. **Check ishmael installation**:
   - Check if `ishmael` command is available
   - If not, guide the user to install it:
     ```bash
     pip install -e /path/to/ishmael/repo
     ```
     or if using conda:
     ```bash
     conda activate <env> && pip install -e /path/to/ishmael/repo
     ```

3. **Check bd initialization**:
   - Run `bd status` to see if bd is initialized
   - If not: `bd init` in the repo where beads should be tracked

4. **Check MCP server configuration**:
   - Read the Claude Code settings file (`.claude/settings.local.json` or `~/.claude/settings.json`)
   - Check if `ishmael-mcp` is configured as an MCP server
   - If not, guide the user to add it. The MCP server config should point to the ishmael package's `mcp_server.py`

5. **Verify**: Run a quick smoke test:
   - Call `list_beads` MCP tool to verify the MCP connection works
   - If it works, report "Setup complete!"
   - If not, help debug the issue

6. **Summary**: Show a checklist of what's installed and configured, with pass/fail for each item.
