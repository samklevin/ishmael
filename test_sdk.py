"""Standalone test — verify text + tool blocks captured."""
import asyncio
import os
import shutil
from pathlib import Path

os.environ.pop("CLAUDECODE", None)
os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

async def main():
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    options = ClaudeAgentOptions(
        allowed_tools=["Read"],
        permission_mode="acceptEdits",
        cwd=str(Path.cwd()),
        max_turns=3,
        cli_path=shutil.which("claude"),
    )

    print("Starting query...")
    output_lines = []
    async for message in query(
        prompt="Read pyproject.toml and list the dependencies.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    for line in block.text.splitlines():
                        output_lines.append(line)
                elif hasattr(block, "name"):
                    output_lines.append(f"[tool: {block.name}]")
        elif isinstance(message, ResultMessage):
            print("ResultMessage received")

    print(f"\n--- Captured {len(output_lines)} output lines ---")
    for line in output_lines:
        print(f"  {line}")

asyncio.run(main())
