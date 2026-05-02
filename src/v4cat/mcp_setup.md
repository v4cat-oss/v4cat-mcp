# Setting up the v4cat MCP server

The v4cat package ships an MCP (Model Context Protocol) server that
exposes every ISA verb as a tool, every analytic view as a
`catalogue://…` resource, and four workflow prompts. This document
walks through wiring it into the common MCP-aware clients.

## Contents

1. [Install](#1-install)
2. [Pick a persistence mode](#2-pick-a-persistence-mode)
3. [VS Code](#3-vs-code)
4. [Claude Desktop](#4-claude-desktop)
5. [Claude Code (CLI)](#5-claude-code-cli)
6. [Codex CLI](#6-codex-cli)
7. [Manual / generic stdio](#7-manual--generic-stdio)
8. [Verify the server is reachable](#8-verify-the-server-is-reachable)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Install

The package is on PyPI. Pick whichever installation method matches
how you run other Python tools.

```sh
# pipx — installs to an isolated, always-available location
pipx install v4cat

# uv tool — newer, faster equivalent
uv tool install v4cat

# global pip
pip install v4cat

# inside a venv
python -m venv .venv && .venv/bin/pip install v4cat
```

After install the `v4cat-mcp` script is on PATH:

```sh
$ which v4cat-mcp
/home/you/.local/bin/v4cat-mcp

$ v4cat-mcp --help
usage: v4cat.mcp_server [-h] [--db DB | --root ROOT] [--default DEFAULT]
…
```

If you don't want a permanent install, run it on demand:

```sh
uvx v4cat-mcp --db /path/to/cat.db
# or
pipx run v4cat -- v4cat-mcp --db /path/to/cat.db
```

## 2. Pick a persistence mode

The server has two mutually-exclusive modes. Pick whichever matches
how the LLM should be allowed to redirect the catalogue file.

### Pinned-file mode — `--db PATH`

```sh
v4cat-mcp --db /path/to/cat.db
```

The server is pinned to one SQLite file. The slot tools
(`list_catalogues`, `open_catalogue`, `create_catalogue`) error out;
the LLM has no string it can use to point the server at a different
file. Best when one project ↔ one catalogue.

### Named-slot mode — `--root DIR [--default SLOT]`

```sh
v4cat-mcp --root /path/to/dir --default mydomain
```

The server is confined to a sandbox directory. Slot tools become
available; clients address catalogues by *slug* (matching
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`), never by path. The server
resolves `<slug>` to `<root>/<slug>.db` and rejects anything that
escapes the root after symlink resolution. Best when one MCP server
should serve several related catalogues that an LLM might switch
between. See [`sandbox.py`](sandbox.py) for the validation rules.

## 3. VS Code

VS Code has native MCP support via `.vscode/mcp.json` (workspace) or
the user-level configuration file (run **MCP: Open User
Configuration** from the command palette).

### Workspace setup — `.vscode/mcp.json`

Create the file at the root of your workspace:

```json
{
  "servers": {
    "v4cat": {
      "type": "stdio",
      "command": "v4cat-mcp",
      "args": ["--db", "${workspaceFolder}/cat.db"]
    }
  }
}
```

Use `${workspaceFolder}` so the path resolves wherever the workspace
is checked out.

### Named-slot variant

```json
{
  "servers": {
    "v4cat": {
      "type": "stdio",
      "command": "v4cat-mcp",
      "args": ["--root", "${workspaceFolder}/.v4cat", "--default", "main"]
    }
  }
}
```

The `.v4cat/` directory will hold one or more `<slug>.db` files; the
server creates them on demand via the `create_catalogue` tool.

### Without an installed `v4cat-mcp` script

If you'd rather not install v4cat globally and keep it scoped to the
workspace, point at uvx (or another runner) instead:

```json
{
  "servers": {
    "v4cat": {
      "type": "stdio",
      "command": "uvx",
      "args": ["v4cat-mcp", "--db", "${workspaceFolder}/cat.db"]
    }
  }
}
```

### User-level (cross-workspace)

Run **MCP: Open User Configuration** from the command palette and
add the same `servers` block. The user-level config applies across
every workspace; useful for catalogues that live outside any
particular project (e.g., a personal knowledge graph at
`~/cat.db`).

### Pinning `v4cat` to a specific version

If you want to lock v4cat at a known version (recommended for
shared workspaces), use `uvx --from`:

```json
{
  "servers": {
    "v4cat": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "v4cat==0.3.0",
        "v4cat-mcp",
        "--db", "${workspaceFolder}/cat.db"
      ]
    }
  }
}
```

## 4. Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows,
or `~/.config/Claude/claude_desktop_config.json` on Linux:

```json
{
  "mcpServers": {
    "v4cat": {
      "command": "v4cat-mcp",
      "args": ["--db", "/Users/you/v4cat/cat.db"]
    }
  }
}
```

Note: Claude Desktop uses `mcpServers` (camelCase) and absolute
paths. Restart Claude Desktop after editing.

## 5. Claude Code (CLI)

```sh
# Workspace-scoped (added to .mcp.json in the current directory)
claude mcp add v4cat -- v4cat-mcp --db ./cat.db

# User-scoped (available everywhere)
claude mcp add --scope user v4cat -- v4cat-mcp --db ~/cat.db
```

The `--` separator passes everything after it as the command and
args to the MCP server.

To verify it's been registered:

```sh
claude mcp list
```

## 6. Codex CLI

OpenAI's Codex CLI configures MCP servers under `mcp_servers` in
`~/.codex/config.toml` (TOML, not JSON):

```toml
[mcp_servers.v4cat]
command = "v4cat-mcp"
args = ["--db", "/Users/you/v4cat/cat.db"]
```

Named-slot variant:

```toml
[mcp_servers.v4cat]
command = "v4cat-mcp"
args = ["--root", "/Users/you/v4cat", "--default", "main"]
```

Without an installed `v4cat-mcp` script:

```toml
[mcp_servers.v4cat]
command = "uvx"
args = ["v4cat-mcp", "--db", "/Users/you/v4cat/cat.db"]
```

Useful Codex-specific knobs:

- **`enabled = false`** — disable the server without removing its
  config block.
- **`enabled_tools = ["introduce_break", "witness", "kquery"]`** —
  restrict to a subset of v4cat's ISA verbs (the
  `disabled_tools` array is applied after, if both are set).
- **`startup_timeout_sec = 15`** — override the default 10s
  startup timeout if your environment is slow.
- **`env = { V4CAT_LOG_LEVEL = "debug" }`** — pass environment
  variables to the server process.

Codex's per-tool approval mode is off by default; if you want a
prompt before every mutating ISA verb runs, override:

```toml
[mcp_servers.v4cat]
command = "v4cat-mcp"
args = ["--db", "/Users/you/v4cat/cat.db"]
default_tools_approval_mode = "approve"

[mcp_servers.v4cat.tools.kquery]
approval_mode = "approve"
```

(Read-only tools like `kquery` are safe to auto-approve; mutating
ones like `introduce_break` are where the prompt is most useful.)

## 7. Manual / generic stdio

Any client that speaks MCP over stdio can launch the server
directly:

```sh
v4cat-mcp --db /path/to/cat.db
```

The server reads JSON-RPC requests on stdin and writes responses on
stdout per the MCP spec. See the
[MCP specification](https://modelcontextprotocol.io/) for the wire
protocol if you're integrating from a non-standard client.

## 8. Verify the server is reachable

After configuring your client, ask it to call a v4cat tool. Two
quick checks:

### Ask the LLM to read the docs

> "Read `catalogue://docs` and tell me what tools and resources
> are available."

The server returns an index listing every tool, resource, and
prompt. If the client reports the server isn't connected, check
your config file syntax and that `v4cat-mcp --help` works in your
shell with the same `command`.

### Ask the LLM to introduce a synthetic break

> "Use the v4cat catalogue to introduce a break called `F1` with
> name 'Test break' and one axis 'spatial', then read it back via
> `catalogue://breaks/F1`."

This exercises both tool use and resource reading. The break
should appear in the response.

### Self-hosting check

> "Read `catalogue://self_hosting` and tell me whether v4cat is
> currently passing its closure check."

Should report `passing: true` and 15 cells in the `11`
(IMPL-and-CAT) cell. This confirms the framework's own seed loaded
correctly. (The exact count may differ in domain-extension
catalogues that scope the check more narrowly.)

## 9. Troubleshooting

### Server not appearing in client

- Check the client's MCP logs. VS Code shows them via
  **MCP: List Servers** → click the server → **Show Output**.
- Run `v4cat-mcp --help` in the same shell environment as the
  client. If the script isn't found, the client's PATH may not
  match yours; use an absolute path in the `command` field.

### "Module not found: mcp"

The runtime dependency `mcp>=1.0` should be pulled in automatically
by `pip install v4cat`. If you get this error, you've installed
v4cat without its dependencies (rare). Re-install:

```sh
pip install --force-reinstall v4cat
```

### "Slot tools error in pinned-file mode"

You launched the server with `--db` but the LLM tried to call a
slot tool. This is by design — `--db` mode locks the server to one
file. Either stop the LLM from calling slot tools, or relaunch the
server with `--root DIR` to enable named-slot mode.

### Catalogue file isn't created

The first `introduce_*` call against a fresh `--db PATH` creates
the file. If the parent directory doesn't exist or isn't writable,
the server raises `sqlite3.OperationalError: unable to open
database file`. Make sure the directory exists; the server doesn't
auto-create it.

### Closure check fails on a domain-extension catalogue

The framework's `Q-supported-claims` refinement declares which
kinds of cells are in scope. A domain extension that adds new
primitives without registering them violates the closure check.
See [`theory.md`](theory.md) § 14 and the
`SelfHostingViolation` payload's `implicit` / `promissory` lists
for the exact gap.

## Further reading

- [`README.md`](README.md) — quick-start and package layout
- [`tutorial.md`](tutorial.md) — operating the catalogue end-to-end
- [`methodology.md`](methodology.md) — the ISA + MCP interface
  in full
- [`theory.md`](theory.md) — foundations (Klein-four, Yoneda+
  Derrida, magma + pointfree, Theorem 14.5)
- [`examples.md`](examples.md) — domain templates
