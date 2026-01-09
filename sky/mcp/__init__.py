"""SkyPilot MCP (Model Context Protocol) Server.

This module provides an MCP server that exposes SkyPilot functionality
to AI assistants and other MCP clients.
"""

from sky.mcp.server import main, mcp

__all__ = ['main', 'mcp']
