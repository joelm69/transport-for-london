"""The platform's MCP server: exposes the read-only tools in src/agent/tools.py over the Model
Context Protocol, so any MCP client (Claude Desktop, Claude Code, another agent) can answer
questions with London's live and historical transport data.

Run locally for Claude Desktop or Claude Code (stdio):
    python -m src.mcp_server.server
"""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from src.agent.tools import TOOLS

INSTRUCTIONS = """Tools for Joel Mutiso's TfL data platform: live TfL feeds, the business
recommendations (last 7 complete London days), read-only SQL on the history, and a search of the
platform's own definitions. Use the tools for every figure; call search_docs before explaining
what a figure means; say which period figures cover (history began in late October 2026)."""

server = MCPServer("tfl-data-platform", instructions=INSTRUCTIONS)
for tool in TOOLS:
    # Every tool only reads: clients may run them without asking for confirmation.
    server.add_tool(tool, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))


if __name__ == "__main__":
    server.run()
