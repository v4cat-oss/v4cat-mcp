"""
v4cat_mcp — MCP server for the v4cat symmetry-break catalogue framework.

This is a thin presentation layer over :mod:`v4cat`. It exposes the v4cat
ISA verbs as Model Context Protocol tools, the analytic views as
addressable ``catalogue://`` resources, and a small set of workflow
prompts. The core catalogue (schema, ISA, kquery) lives in the v4cat
package; this package depends on it.

Run via the console script::

    v4cat-mcp [--db PATH | --root DIR [--default SLOT]]

or as a module::

    python -m v4cat_mcp [...]

See ``v4cat_mcp/setup.md`` (also exposed as ``catalogue://mcp_setup``)
for per-client wiring instructions (VS Code, Claude Desktop, Claude
Code, Codex CLI).
"""
from .server import main

__all__ = ['main']
