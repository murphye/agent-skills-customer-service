"""
LangGraph Server entrypoint — exports `agent` for `langgraph dev`.

Uses the deepagents skills framework with progressive disclosure. A short
system prompt nudges the agent to always load the customer-service skill
via read_file, keeping the skills mechanism intact.

Note: langchain_mcp_adapters 0.1.0+ creates a new stdio subprocess per tool
call. The MCP servers use file-backed stores so state persists across calls.

Usage:
    cd langgraph && langgraph dev
"""
import asyncio

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

# ── Build the agent with MCP tools ──────────────────────────

client = MultiServerMCPClient(
    {
        "orders": {
            "transport": "stdio",
            "command": "uv",
            "args": ["run", "../.claude/skills/customer-service/mcp/orders.py"],
        },
        "tickets": {
            "transport": "stdio",
            "command": "uv",
            "args": ["run", "../.claude/skills/customer-service/mcp/tickets.py"],
        },
    }
)
mcp_tools = asyncio.new_event_loop().run_until_complete(client.get_tools())

agent = create_deep_agent(
    model="claude-sonnet-4-6",
    tools=mcp_tools,
    skills=[".claude/skills/"],
    backend=FilesystemBackend(root_dir=".."),
    system_prompt="You are a customer service agent. Always read and follow the customer-service skill before responding.",
)
