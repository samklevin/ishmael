---
name: ish:templates
description: Use when the user wants to learn about, create, or manage ishmael workflow templates
disable-model-invocation: true
---

# Workflow Templates

Templates define reusable chains of beads with dependencies. They live as YAML files in `~/.ishmael/templates/`.

## Template format

```yaml
name: my-workflow
description: What this workflow does

params:
  feature_name:
    description: Name of the feature to build
  repo_path:
    description: Absolute path to the target repo

steps:
  - id: design
    title: "Design {feature_name}"
    description: |
      Explore the codebase and design the approach for {feature_name}.
      Write a design doc in docs/design-{feature_name}.md.
    type: auto

  - id: implement
    title: "Implement {feature_name}"
    description: |
      Implement {feature_name} following the design doc.
    type: auto
    blocked_by: [design]

  - id: review
    title: "Review {feature_name}"
    description: |
      Review the implementation for correctness, style, and test coverage.
    type: auto
    blocked_by: [implement]
```

### Fields

- **name**: Template identifier (also derived from filename)
- **description**: What the workflow does
- **params**: Named parameters substituted into step titles/descriptions via `{param_name}`
- **steps**: Ordered list of bead definitions
  - **id**: Unique step identifier (used in `blocked_by` references)
  - **title**: Bead title (supports `{param}` substitution)
  - **description**: Agent-oriented instructions (supports `{param}` substitution)
  - **prompt**: Alternative to description (also supports substitution)
  - **type**: `auto` (default, picked up by orchestrator) or `manual`
  - **blocked_by**: List of step IDs that must complete first

## Steps

1. **Determine intent**: Is the user asking to:
   - **List** existing templates? -> Call `list_templates` MCP tool
   - **View** a specific template? -> Call `list_templates` and show the matching one in detail
   - **Create** a new template? -> Help them write the YAML (proceed to step 2)
   - **Run** a template? -> Suggest `/ish:dispatch` for ad-hoc plans, or show CLI usage: `ishmael workflow run <name> --repo . --param key=value`

2. **Help write a template**: If creating a new template:
   - Ask what the workflow should accomplish
   - Break it into sequential/parallel steps with clear boundaries
   - Write agent-oriented descriptions for each step (self-contained, specific acceptance criteria)
   - Identify dependencies between steps
   - Write the YAML file to `~/.ishmael/templates/<name>.yaml`

3. **Show available commands**:
   - CLI: `ishmael workflow list`, `ishmael workflow run <name> --repo . --param key=value`
   - MCP: `list_templates`, `instantiate_workflow`
   - Skill: `/ish:dispatch` creates bead chains from conversation context (no template file needed)
