---
name: ish:dispatch-workflow
description: Use when the user wants to run/instantiate a workflow template to create a chain of beads
disable-model-invocation: true
---

# Dispatch a Workflow Template

List available workflow templates and instantiate one, creating a chain of beads with dependencies that ishmael agents will execute.

## Steps

1. **List templates**: Call the `list_templates` MCP tool to show what's available. If no templates exist, tell the user and suggest `/ish:templates` to learn how to create one.

2. **Select template**: If the user provided a template name as an argument (e.g. `/ish:dispatch-workflow story`), use that. Otherwise, show the list and ask which one to run.

3. **Determine repo and branch**: Check the current working directory for a `.git` directory. Use the absolute path to the repo root and default branch to `main`. If the user specifies a branch, use that instead.

4. **Gather parameters**: Check what parameters the template requires. If the user provided them inline (e.g. `/ish:dispatch-workflow story story_id=2-1`), parse those. Otherwise, prompt for each required parameter, showing its description.

5. **Confirm**: Show the user a summary before creating beads:
   - Template name
   - Repo and branch
   - Parameters
   - Number of steps and their dependency chain
   
   Ask for confirmation before proceeding.

6. **Instantiate**: Call `instantiate_workflow` MCP tool with:
   - `template_name`: the selected template
   - `repo`: absolute path to the git repository
   - `branch`: branch name
   - `params`: JSON string of parameters (e.g. `{"story_id": "2-1"}`)

7. **Report**: Show the results:
   - Each bead's ID, title, and what it's blocked by
   - A visual dependency graph (simple text format)
   - Total number of beads created
   - Note that the orchestrator will automatically pick up ready beads

   If any steps had errors, show those clearly and suggest how to fix them.
