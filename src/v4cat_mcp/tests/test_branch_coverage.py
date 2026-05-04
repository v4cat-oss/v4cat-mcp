"""
Branch-coverage tests — MCP-side residue branches.

The v4cat-core branch-coverage tests live in v4cat itself; this file
covers branches inside v4cat_mcp.server (lazy catalogue init, slot
swap, MCP tool/resource bodies, CLI argument handling, and the
`__main__` guard).

Each test names the branch(es) it covers in its docstring so future
RFS fires can re-run the analysis and recognise these as orbit
positions of the kquery-on-coverage primitive.

Run as a script::

    python -m v4cat_mcp.tests.test_branch_coverage
"""
from __future__ import annotations

import asyncio
import json
import runpy
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

from v4cat import SymmetryCatalogue

import v4cat_mcp.server as srv
from v4cat_mcp.server import server, set_catalogue


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

async def call_tool(tool_name, /, **kwargs):
    result = await server.call_tool(tool_name, kwargs)
    if isinstance(result, tuple) and len(result) == 2:
        _, structured = result
        if isinstance(structured, dict) and set(structured.keys()) == {'result'}:
            return structured['result']
        return structured
    if isinstance(result, list) and result and hasattr(result[0], 'text'):
        text = result[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return result


async def read_resource(uri: str):
    result = await server.read_resource(uri)
    items = list(result)
    if items and hasattr(items[0], 'content'):
        text = items[0].content
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return items


def fresh_populated_catalogue():
    cat = SymmetryCatalogue(':memory:')
    set_catalogue(cat)
    cat.introduce_object('alpha', 'Alpha', year=1980, catalogue_order=1)
    cat.introduce_break('F1', 'Spatial test', axes=['spatial'])
    cat.witness('alpha', 'F1', 'origin')
    cat.witness('alpha', 'F1', 'catalogue-introduces')
    cat.commit()
    return cat


# -----------------------------------------------------------------------------
# Lazy catalogue + swap
# -----------------------------------------------------------------------------

async def test_get_catalogue_lazy_init():
    """server.py — get_catalogue() lazily creates _cat when None."""
    saved = srv._cat
    srv._cat = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tf:
            tf_path = tf.name
        old_default = srv.DEFAULT_DB_PATH
        srv.DEFAULT_DB_PATH = tf_path
        try:
            cat = srv.get_catalogue()
            assert cat is not None
            assert srv._cat is cat
        finally:
            srv.DEFAULT_DB_PATH = old_default
            Path(tf_path).unlink(missing_ok=True)
    finally:
        srv._cat = saved


async def test_swap_active_swallows_close_error():
    """server.py — _swap_active swallows close exception."""
    class BrokenCat:
        def close(self):
            raise RuntimeError('close failed')
    saved = srv._cat
    srv._cat = BrokenCat()
    try:
        new = SymmetryCatalogue(':memory:')
        srv._swap_active(new, 'test_slot')
        assert srv._cat is new
        assert srv._active_slot == 'test_slot'
    finally:
        srv._cat = saved
        srv._active_slot = None


async def test_swap_active_with_no_prior_catalogue():
    """server.py — _swap_active when _cat is None (False side)."""
    saved = srv._cat
    saved_slot = srv._active_slot
    srv._cat = None
    srv._active_slot = None
    try:
        new = SymmetryCatalogue(':memory:')
        srv._swap_active(new, 'fresh_slot')
        assert srv._cat is new
        assert srv._active_slot == 'fresh_slot'
    finally:
        srv._cat = saved
        srv._active_slot = saved_slot


# -----------------------------------------------------------------------------
# Tool bodies
# -----------------------------------------------------------------------------

async def test_introduce_tension_tool():
    """server.py — introduce_tension MCP tool body."""
    fresh_populated_catalogue()
    result = await call_tool(
        'introduce_tension',
        id='T_mcp', name='MCP-introduced tension',
        description='via MCP', breaks_involved=['F1'],
    )
    assert result['ok'] is True
    assert result['id'] == 'T_mcp'


async def test_query_first_seen_tool():
    """server.py — query_first_seen tool returns None for unknown."""
    fresh_populated_catalogue()
    result = await call_tool('query_first_seen', break_number='F-unknown')
    assert result is None


async def test_query_inherited_breaks_tool():
    """server.py — query_inherited_breaks tool body."""
    cat = fresh_populated_catalogue()
    cat.introduce_object(
        'beta', 'Beta', year=1990,
        lineage=[('alpha', 'descended-from')],
    )
    cat.commit()
    result = await call_tool('query_inherited_breaks', object_id='beta')
    assert isinstance(result, list)
    inherited = {r['break_number'] for r in result}
    assert 'F1' in inherited


# -----------------------------------------------------------------------------
# Resource bodies
# -----------------------------------------------------------------------------

async def test_get_break_404_for_missing():
    """server.py — get_break returns error for unknown."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://breaks/Q-nonexistent')
    assert 'error' in result


async def test_list_objects_resource():
    """server.py — list_objects resource body."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://objects')
    assert isinstance(result, list)
    assert any(o['id'] == 'alpha' for o in result)


async def test_get_object_404_for_missing():
    """server.py — get_object returns error for unknown."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://objects/nonexistent')
    assert 'error' in result


async def test_tensions_resource():
    """server.py — tensions resource body."""
    cat = fresh_populated_catalogue()
    cat.introduce_tension('T1', 'Sample tension')
    cat.commit()
    result = await read_resource('catalogue://tensions')
    assert isinstance(result, list)
    assert any(t['id'] == 'T1' for t in result)


async def test_violations_resource_with_valid_rule():
    """server.py — catalogue://violations/{rule} parametric
    consistency-rule resource (success path)."""
    cat = fresh_populated_catalogue()
    with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False) as f:
        f.write(
            "CREATE VIEW IF NOT EXISTS demo_violations AS "
            "SELECT 'sentinel' AS spec_id WHERE 1 = 0;"
        )
        f.flush()
        cat.load_extension(f.name)
    cat.commit()
    result = await read_resource('catalogue://violations/demo')
    assert result == []


async def test_violations_resource_rejects_bad_rule_name():
    """server.py — catalogue://violations/{rule} returns an error
    object when the rule name is not a valid identifier."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://violations/1bad')
    assert isinstance(result, dict)
    assert 'error' in result
    assert 'identifier' in result['error']


async def test_axes_resource():
    """server.py — axes resource body."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://axes')
    assert isinstance(result, dict)


async def test_mixed_breaks_resource():
    """server.py — mixed_breaks resource body."""
    cat = fresh_populated_catalogue()
    cat.introduce_break(
        'F-mixed', 'Mixed-axis break',
        axes=['spatial', 'temporal'],
    )
    cat.commit()
    result = await read_resource('catalogue://mixed_breaks')
    assert isinstance(result, list)


async def test_agent_witnesses_resource():
    """server.py — agent_witnesses resource body."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://agent_witnesses')
    assert isinstance(result, list)


async def test_spec_axes_resource():
    """server.py — spec_axes resource body."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://spec_axes')
    assert isinstance(result, list)


async def test_top_originators_resource():
    """server.py — top_originators resource body."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://top_originators')
    assert isinstance(result, list)


async def test_self_hosting_resource_when_self_hosting():
    """server.py — self_hosting resource when supported."""
    fresh_populated_catalogue()
    result = await read_resource('catalogue://self_hosting')
    assert result['supported'] is True
    assert result['passing'] is True
    assert 'cells' in result


async def test_self_hosting_resource_when_not_self_hosting():
    """server.py — self_hosting resource when not supported."""
    cat = SymmetryCatalogue(
        ':memory:', bootstrap=True, check_self_hosting=False,
    )
    set_catalogue(cat)
    result = await read_resource('catalogue://self_hosting')
    assert result['supported'] is False
    assert result['passing'] is None


# -----------------------------------------------------------------------------
# CLI / main()
# -----------------------------------------------------------------------------

async def test_main_default_without_root_errors():
    """server.py — --default without --root errors out."""
    with patch('sys.argv', ['v4cat-mcp', '--default', 'foo']):
        with patch.object(srv.server, 'run'):
            try:
                srv.main()
                assert False, "expected SystemExit"
            except SystemExit:
                pass


async def test_main_root_only():
    """server.py — --root without --default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch('sys.argv', ['v4cat-mcp', '--root', tmpdir]):
            with patch.object(srv.server, 'run'):
                srv.main()


async def test_main_root_with_default_creates_slot():
    """server.py — --root + --default opens slot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, 'mydomain.db').touch()
        with patch('sys.argv', ['v4cat-mcp', '--root', tmpdir,
                                 '--default', 'mydomain']):
            with patch.object(srv.server, 'run'):
                srv.main()


async def test_main_db_path_only():
    """server.py — --db sets DEFAULT_DB_PATH."""
    saved_default = srv.DEFAULT_DB_PATH
    saved_cat = srv._cat
    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tf:
            tf_path = tf.name
        with patch('sys.argv', ['v4cat-mcp', '--db', tf_path]):
            with patch.object(srv.server, 'run'):
                srv.main()
        assert srv.DEFAULT_DB_PATH == tf_path
        Path(tf_path).unlink(missing_ok=True)
    finally:
        srv.DEFAULT_DB_PATH = saved_default
        srv._cat = saved_cat


async def test_main_no_args_uses_defaults():
    """server.py — no --root and no --db (False side)."""
    saved_default = srv.DEFAULT_DB_PATH
    saved_cat = srv._cat
    try:
        with patch('sys.argv', ['v4cat-mcp']):
            with patch.object(srv.server, 'run'):
                srv.main()
    finally:
        srv.DEFAULT_DB_PATH = saved_default
        srv._cat = saved_cat


async def test_module_main_guard():
    """server.py — `if __name__ == '__main__': main()`.

    Run the module as a script via runpy. runpy re-imports the
    module under name '__main__', so we patch the FastMCP.run
    method at the *class* level so the freshly-instantiated server
    inherits the no-op.
    """
    from mcp.server.fastmcp import FastMCP
    saved_default = srv.DEFAULT_DB_PATH
    saved_cat = srv._cat
    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tf:
            tf_path = tf.name
        with patch('sys.argv', ['v4cat-mcp', '--db', tf_path]):
            with patch.object(FastMCP, 'run', lambda self, *a, **kw: None):
                runpy.run_module(
                    'v4cat_mcp.server', run_name='__main__',
                )
        Path(tf_path).unlink(missing_ok=True)
    finally:
        srv.DEFAULT_DB_PATH = saved_default
        srv._cat = saved_cat


# -----------------------------------------------------------------------------
# Test harness — script-style runner; pytest discovers the async tests
# -----------------------------------------------------------------------------

ALL_TESTS = [
    test_get_catalogue_lazy_init,
    test_swap_active_swallows_close_error,
    test_swap_active_with_no_prior_catalogue,
    test_introduce_tension_tool,
    test_query_first_seen_tool,
    test_query_inherited_breaks_tool,
    test_get_break_404_for_missing,
    test_list_objects_resource,
    test_get_object_404_for_missing,
    test_tensions_resource,
    test_violations_resource_with_valid_rule,
    test_violations_resource_rejects_bad_rule_name,
    test_axes_resource,
    test_mixed_breaks_resource,
    test_agent_witnesses_resource,
    test_spec_axes_resource,
    test_top_originators_resource,
    test_self_hosting_resource_when_self_hosting,
    test_self_hosting_resource_when_not_self_hosting,
    test_main_default_without_root_errors,
    test_main_root_only,
    test_main_root_with_default_creates_slot,
    test_main_db_path_only,
    test_main_no_args_uses_defaults,
    test_module_main_guard,
]


async def run_all():
    passed = 0
    failed = 0
    for test in ALL_TESTS:
        try:
            await test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def main() -> int:
    return asyncio.run(run_all())


if __name__ == '__main__':
    sys.exit(main())
