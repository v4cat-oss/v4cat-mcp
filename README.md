# v4cat-mcp — MCP server for v4cat

Model Context Protocol server that exposes the [v4cat][core] symmetry-break
catalogue ISA as MCP tools, resources, and prompts. This is a thin
presentation layer over the [v4cat][core] core; it does not duplicate
schema, kquery, or any other catalogue logic.

[core]: https://github.com/v4cat-oss/v4cat

## Install

```sh
pip install v4cat-mcp     # pulls v4cat as a dependency
```

## Run

Pinned-file mode — the server is bound to one SQLite file:

```sh
v4cat-mcp --db /path/to/cat.db
```

Named-slot (sandbox) mode — the server is confined to a directory and
the LLM picks slots by slug name:

```sh
v4cat-mcp --root /path/to/dir [--default mydomain]
```

Equivalent to `python -m v4cat_mcp [...]`.

Connects via stdio, the standard MCP transport. Configure your
MCP-aware client (VS Code, Claude Desktop, Claude Code, Codex CLI,
custom agents) to talk to it — see [`setup.md`](src/v4cat_mcp/setup.md)
(also exposed as the `catalogue://mcp_setup` MCP resource at runtime)
for per-client wiring instructions.

## What it exposes

- **Tools** — one per ISA verb: `introduce_break`, `introduce_object`,
  `witness`, `refine`, `defer`, `promote`, `boundary`, plus slot tools
  (`list_catalogues`, `open_catalogue`, `create_catalogue`) in named-slot
  mode.
- **Resources** — `catalogue://breaks`, `catalogue://breaks/{n}`,
  `catalogue://retroactive`, `catalogue://violations/{rule}`, the
  Klein-four read primitive `catalogue://kquery`, and ten user-facing
  documentation resources (`catalogue://methodology`, `theory`,
  `tutorial`, `examples`, `readme`, `mcp_setup`, `rigorous_use`,
  `python_api`, `hosted_skills`, `hosted_skill_usage`).
- **Prompts** — workflow templates (`analyze_new_object`,
  `audit_md_vs_sql`, `next_object`, `snap_to_grid_check`).

The methodology, theory, tutorial, and other v4cat documentation
resources are served from the v4cat package's installed data — this
package only owns its own [`setup.md`](src/v4cat_mcp/setup.md).

## Layout

```text
v4cat-mcp/
├── pyproject.toml           pip-installable package metadata
├── LICENSE
├── README.md                this file
└── src/v4cat_mcp/
    ├── __init__.py
    ├── __main__.py          enables `python -m v4cat_mcp`
    ├── server.py            FastMCP server (tools/resources/prompts)
    ├── setup.md             per-client wiring instructions (catalogue://mcp_setup)
    └── tests/
        └── test_server.py   in-process tests against the FastMCP API
```

## Tests

```sh
pip install -e ".[dev]"
pytest
```

The tests exercise tools, resources, and prompts via FastMCP's
in-process API against a fresh in-memory v4cat catalogue.

## Relationship to v4cat

The catalogue is a graph; this server is one *presentation* of that
graph. The split between v4cat (core) and v4cat-mcp (this server) is
itself catalogued in [v4cat's cotype][cotype-shadow] as a
distribution-seam shadow.

[cotype-shadow]: https://github.com/v4cat-oss/v4cat/blob/main/cotype/shadow_distribution_seam_mcp.md
