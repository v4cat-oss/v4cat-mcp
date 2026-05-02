"""
Framework tests for v4cat.mcp_server.

Exercises tools, resources, and prompts via FastMCP's in-process API.
Uses synthetic mini-domain data; no dependency on processor catalogue.

Run as a script::

    python -m v4cat.tests.test_mcp
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback

import tempfile
from pathlib import Path

from v4cat import SymmetryCatalogue
from v4cat.mcp_server import server, set_catalogue, set_root
from v4cat.sandbox import CatalogueRoot


# -----------------------------------------------------------------------------
# Fixture: fresh in-memory catalogue, injected into the server
# -----------------------------------------------------------------------------

def fresh_catalogue() -> SymmetryCatalogue:
    cat = SymmetryCatalogue(':memory:')
    set_catalogue(cat)
    return cat


def populated_catalogue() -> SymmetryCatalogue:
    """A catalogue with a small synthetic domain pre-populated.

    Three objects, two breaks, witness + refinement edges. Used by
    resource and prompt tests.
    """
    cat = fresh_catalogue()
    cat.introduce_object('alpha', 'Alpha', year=1980, catalogue_order=1)
    cat.introduce_object('beta',  'Beta',  year=1985, catalogue_order=2,
                         lineage=[('alpha', 'descended-from')])
    cat.introduce_object('gamma', 'Gamma', year=1990, catalogue_order=3,
                         lineage=[('beta',  'descended-from')])
    cat.introduce_break('F1', 'Spatial test', short_desc='spatial only',
                        axes=['spatial'])
    cat.introduce_break('F2', 'Mixed-axis test', axes=['spatial', 'temporal'])
    cat.witness('alpha', 'F1', 'origin')
    cat.witness('alpha', 'F1', 'catalogue-introduces')
    cat.witness('beta',  'F2', 'origin')
    cat.witness('beta',  'F2', 'catalogue-introduces')
    cat.witness('beta',  'F1', 'inherits')
    cat.refine('F2', 'beta', 'foo-extension', description='details')
    cat.commit()
    return cat


# -----------------------------------------------------------------------------
# Helpers — FastMCP test invocation
# -----------------------------------------------------------------------------

async def call_tool(tool_name, /, **kwargs):
    """Call a registered MCP tool by name and return its parsed result.

    FastMCP returns either ``list[TextContent]`` or
    ``(content, structured)`` tuple depending on the tool's annotation.
    """
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


async def get_prompt(name: str, **kwargs):
    return await server.get_prompt(name, kwargs)


# -----------------------------------------------------------------------------
# Tool tests
# -----------------------------------------------------------------------------

async def test_introduce_break_tool():
    fresh_catalogue()
    result = await call_tool('introduce_break',
                             number='F-test', name='Test',
                             axes=['spatial'])
    assert result['ok'] is True
    assert result['number'] == 'F-test'


async def test_introduce_object_with_lineage():
    fresh_catalogue()
    await call_tool('introduce_object',
                    id='root',  name='Root',  year=1980)
    result = await call_tool('introduce_object',
                             id='child', name='Child', year=1990,
                             lineage=[['root', 'descended-from']])
    assert result['ok'] is True
    chain = await call_tool('query_lineage', object_id='child')
    ancestors = [r['ancestor'] for r in chain]
    assert 'root' in ancestors


async def test_introduce_object_with_attrs():
    """Domain-specific attrs flow through MCP into spec_attributes."""
    fresh_catalogue()
    await call_tool('introduce_object',
                    id='80386', name='Intel 80386',
                    year=1985,
                    attrs={'vendor': 'Intel', 'family': 'x86',
                           'data_bits': 32, 'address_bits': 32})
    detail = await read_resource('catalogue://objects/80386')
    assert 'attributes' in detail
    attrs = detail['attributes']
    assert attrs['vendor'] == 'Intel'
    assert attrs['family'] == 'x86'
    assert attrs['data_bits'] == '32'
    assert attrs['address_bits'] == '32'


async def test_witness_and_origin_derivation():
    fresh_catalogue()
    await call_tool('introduce_object', id='proc', name='P', year=1990)
    await call_tool('introduce_break',
                    number='F-w', name='Witness test', axes=['temporal'])
    await call_tool('witness',
                    subject='proc', break_number='F-w',
                    kind='catalogue-introduces')
    await call_tool('witness',
                    subject='proc', break_number='F-w', kind='origin')
    origin = await call_tool('query_origin', break_number='F-w')
    assert origin['originator_id']   == 'proc'
    assert origin['originated_year'] == 1990


async def test_chronological_origin_emerges_via_tools():
    """The methodology's central claim: no RETRO verb needed."""
    fresh_catalogue()
    await call_tool('introduce_object', id='later',   name='Later',   year=1990)
    await call_tool('introduce_object', id='earlier', name='Earlier', year=1980)
    await call_tool('introduce_break',  number='F-c', name='Test',    axes=['spatial'])

    await call_tool('witness',
                    subject='later', break_number='F-c',
                    kind='catalogue-introduces')
    await call_tool('witness',
                    subject='later', break_number='F-c', kind='origin')
    o = await call_tool('query_origin', break_number='F-c')
    assert o['originator_id'] == 'later'

    # Add an earlier-year origin witness; MIN-year automatically picks it.
    await call_tool('witness',
                    subject='earlier', break_number='F-c', kind='origin')

    o = await call_tool('query_origin', break_number='F-c')
    assert o['originator_id']   == 'earlier'
    assert o['originated_year'] == 1980


async def test_refine_tool():
    fresh_catalogue()
    await call_tool('introduce_object', id='spec', name='S', year=2000)
    await call_tool('introduce_break',  number='F-r', name='R',
                    axes=['spatial'])
    await call_tool('witness', subject='spec', break_number='F-r',
                    kind='refines')
    await call_tool('refine', break_number='F-r', object_id='spec',
                    name='alpha', description='alpha refinement')
    await call_tool('refine', break_number='F-r', object_id='spec',
                    name='beta',  description='beta refinement')

    detail = await read_resource('catalogue://breaks/F-r')
    refinements = detail['refinements']
    names = sorted(r['name'] for r in refinements)
    assert names == ['alpha', 'beta']


async def test_defer_promote_via_tools():
    fresh_catalogue()
    await call_tool('introduce_object', id='a', name='A', year=2000)
    await call_tool('introduce_object', id='b', name='B', year=2002)
    await call_tool('introduce_break',  number='F-d', name='D', axes=['spatial'])
    await call_tool('witness', subject='a', break_number='F-d',
                    kind='catalogue-introduces')

    await call_tool('defer', break_number='F-d', by='a')
    s = await call_tool('query_status', break_number='F-d')
    assert s == 'deferred'

    await call_tool('promote', break_number='F-d', by='b')
    s = await call_tool('query_status', break_number='F-d')
    assert s == 'active'


async def test_boundary_via_tool():
    fresh_catalogue()
    await call_tool('introduce_object', id='spec', name='S', year=2000)
    await call_tool('introduce_break', number='F-b', name='B', axes=['parallel'])
    await call_tool('boundary',
                    break_number='F-b',
                    reason='deliberate non-extension',
                    by='spec')
    s = await call_tool('query_status', break_number='F-b')
    assert s == 'sibling-boundary'


async def test_query_wedge_tool():
    fresh_catalogue()
    result = await call_tool('query_wedge',
                             set_a=['a', 'b', 'c'],
                             set_b=['b', 'c', 'd'])
    assert result['in_a_not_b'] == ['a']
    assert result['in_b_not_a'] == ['d']
    assert result['in_both']    == ['b', 'c']


async def test_kquery_tool_default():
    fresh_catalogue()
    result = await call_tool('kquery',
                             set_a=['a', 'b', 'c'],
                             set_b=['b', 'c', 'd'])
    assert result['11'] == ['b', 'c']
    assert result['10'] == ['a']
    assert result['01'] == ['d']
    assert result['00'] == []


async def test_kquery_tool_with_universe():
    fresh_catalogue()
    result = await call_tool('kquery',
                             set_a=['a', 'b'],
                             set_b=['b', 'c'],
                             universe=['a', 'b', 'c', 'd', 'e'])
    assert result['00'] == ['d', 'e']
    assert result['11'] == ['b']


async def test_tropical_min_tool_recovers_origin():
    """The generic tropical operator over the year column recovers
    originator semantics."""
    populated_catalogue()
    rows = await call_tool(
        'tropical_min',
        axis_column='year',
        witness_kinds=['origin', 'catalogue-introduces'],
    )
    by_break = {r['break_number']: r for r in rows}
    assert by_break['F1']['spec_id']    == 'alpha'
    assert by_break['F1']['axis_value'] == 1980


async def test_tropical_min_tool_over_catalogue_order():
    """Same operator with axis_column='catalogue_order' recovers
    first-seen semantics."""
    populated_catalogue()
    rows = await call_tool(
        'tropical_min',
        axis_column='catalogue_order',
        witness_kinds=['catalogue-introduces'],
    )
    by_break = {r['break_number']: r for r in rows}
    assert by_break['F1']['spec_id']    == 'alpha'
    assert by_break['F1']['axis_value'] == 1


async def test_kquery_tool_emit_subset():
    fresh_catalogue()
    result = await call_tool('kquery',
                             set_a=['a', 'b'],
                             set_b=['b', 'c'],
                             emit=['10', '01'])
    assert set(result.keys()) == {'10', '01'}


# -----------------------------------------------------------------------------
# Resource tests
# -----------------------------------------------------------------------------

async def test_breaks_resource():
    populated_catalogue()
    breaks = await read_resource('catalogue://breaks')
    assert isinstance(breaks, list)
    numbers = {b['number'] for b in breaks}
    assert {'F1', 'F2'} <= numbers


async def test_break_detail_resource():
    populated_catalogue()
    detail = await read_resource('catalogue://breaks/F2')
    assert detail['number'] == 'F2'
    assert 'witnesses' in detail
    assert 'refinements' in detail
    spec_ids = {w['spec_id'] for w in detail['witnesses']}
    assert 'beta' in spec_ids


async def test_object_detail_resource():
    populated_catalogue()
    detail = await read_resource('catalogue://objects/gamma')
    assert detail['id'] == 'gamma'
    assert 'witnesses' in detail
    assert 'lineage' in detail
    assert 'inherited_breaks' in detail
    inherited = {b['break_number'] for b in detail['inherited_breaks']}
    assert 'F1' in inherited
    assert 'F2' in inherited


async def test_retroactive_resource_empty_for_synthetic():
    populated_catalogue()
    rows = await read_resource('catalogue://retroactive')
    assert rows == []


async def test_axes_resource():
    populated_catalogue()
    dist = await read_resource('catalogue://axes')
    assert dist['spatial']  == 2
    assert dist['temporal'] == 1


async def test_lineage_resource_template():
    populated_catalogue()
    chain = await read_resource('catalogue://lineages/gamma')
    ancestors = [r['ancestor'] for r in chain]
    assert ancestors == ['beta', 'alpha']


# -----------------------------------------------------------------------------
# Documentation resources
# -----------------------------------------------------------------------------

async def test_docs_index_lists_resources():
    """catalogue://docs is the entry point for any LLM client."""
    fresh_catalogue()
    text = await read_resource('catalogue://docs')
    assert isinstance(text, str)
    # Mentions each doc resource
    for name in ('readme', 'tutorial', 'methodology', 'theory', 'examples'):
        assert f'catalogue://{name}' in text
    # Mentions ISA verbs and read tools
    assert 'introduce_break' in text
    assert 'kquery' in text


async def test_methodology_doc_resource():
    fresh_catalogue()
    text = await read_resource('catalogue://methodology')
    assert isinstance(text, str)
    assert 'KQUERY' in text or 'Klein-four' in text


async def test_theory_doc_resource():
    fresh_catalogue()
    text = await read_resource('catalogue://theory')
    assert isinstance(text, str)
    # Theory doc names its core sections
    assert 'shadow-architecture' in text.lower() or 'shadow architecture' in text.lower()
    assert 'Yoneda' in text
    assert 'Derrid' in text     # 'Derrida' or 'Derridean'
    assert 'magma' in text.lower()


async def test_tutorial_doc_resource():
    fresh_catalogue()
    text = await read_resource('catalogue://tutorial')
    assert isinstance(text, str)
    # Tutorial walks through the verbs
    assert 'introduce_object' in text
    assert 'KQUERY' in text or 'kquery' in text
    assert 'retroactive' in text.lower()


async def test_examples_doc_resource():
    fresh_catalogue()
    text = await read_resource('catalogue://examples')
    assert isinstance(text, str)
    # Examples covers multiple domains
    assert 'Programming languages' in text
    assert 'file system' in text.lower() or 'File systems' in text


async def test_readme_doc_resource():
    fresh_catalogue()
    text = await read_resource('catalogue://readme')
    assert isinstance(text, str)
    # README has the quick-start
    assert 'Quick start' in text or 'quick-start' in text.lower()


# -----------------------------------------------------------------------------
# Prompt tests
# -----------------------------------------------------------------------------

async def test_analyze_new_processor_prompt():
    populated_catalogue()
    result = await get_prompt('analyze_new_processor',
                              spec_doc_url='http://example.com/x.pdf')
    text = result.messages[0].content.text
    assert 'http://example.com/x.pdf' in text
    assert 'introduce_object' in text


async def test_audit_md_vs_sql_prompt():
    populated_catalogue()
    result = await get_prompt('audit_md_vs_sql',
                              md_path='symmetries.md',
                              sql_path='symmetries.sql')
    text = result.messages[0].content.text
    assert 'symmetries.md' in text
    assert 'symmetries.sql' in text


async def test_next_processor_prompt_uses_current_catalogue():
    """The next_processor prompt embeds recent catalogue state — it
    runs queries against the live catalogue."""
    populated_catalogue()
    result = await get_prompt('next_processor', domain='witness-object')
    text = result.messages[0].content.text
    assert 'Recent additions' in text
    assert 'Top originators' in text
    # Synthetic domain has alpha/beta/gamma; one should appear
    assert ('Alpha' in text or 'Beta' in text or 'Gamma' in text)


async def test_snap_to_grid_prompt():
    populated_catalogue()
    result = await get_prompt(
        'snap_to_grid_check',
        deliverable_description='Test deliverable'
    )
    text = result.messages[0].content.text
    assert 'Test deliverable' in text


# -----------------------------------------------------------------------------
# Integration: full session
# -----------------------------------------------------------------------------

async def test_full_session_synthetic():
    """Simulate a client extending the catalogue with a new object."""
    populated_catalogue()

    await call_tool('introduce_object',
                    id='delta', name='Delta', year=1995,
                    lineage=[['gamma', 'descended-from']])
    await call_tool('introduce_break',
                    number='F-new', name='New break',
                    axes=['equivalential'])
    await call_tool('witness',
                    subject='delta', break_number='F-new',
                    kind='catalogue-introduces')
    await call_tool('witness',
                    subject='delta', break_number='F-new', kind='origin')

    # Inherited breaks from the full alpha→beta→gamma→delta chain
    detail = await read_resource('catalogue://objects/delta')
    inherited = {b['break_number'] for b in detail['inherited_breaks']}
    assert 'F1' in inherited
    assert 'F2' in inherited

    # The new break has delta as originator
    new_break = await read_resource('catalogue://breaks/F-new')
    assert new_break['originator_name'] == 'Delta'


# -----------------------------------------------------------------------------
# Slot mode — list / open / create_catalogue tools
# -----------------------------------------------------------------------------

async def test_slot_tools_error_in_pinned_mode():
    """In pinned-file mode (no --root), slot tools raise."""
    set_root(None)
    fresh_catalogue()
    try:
        await call_tool('list_catalogues')
    except Exception as e:
        assert 'pinned-file mode' in str(e) or 'without --root' in str(e)
        return
    raise AssertionError('expected slot tool to error without --root')


async def test_create_then_list_slots():
    """create_catalogue creates a slot under root, list_catalogues
    returns it, active slot is set."""
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))

    listing = await call_tool('list_catalogues')
    assert listing['slots'] == []
    assert listing['active'] is None

    created = await call_tool('create_catalogue', name='alpha')
    assert created['ok'] is True
    assert created['active'] == 'alpha'
    assert (tmp / 'alpha.db').exists()

    listing = await call_tool('list_catalogues')
    assert listing['slots'] == ['alpha']
    assert listing['active'] == 'alpha'

    set_root(None)


async def test_open_existing_slot_switches_active():
    """create alpha, create beta (now active), open_catalogue alpha
    → alpha is active again."""
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))

    await call_tool('create_catalogue', name='alpha')
    await call_tool('create_catalogue', name='beta')
    listing = await call_tool('list_catalogues')
    assert listing['active'] == 'beta'
    assert set(listing['slots']) == {'alpha', 'beta'}

    opened = await call_tool('open_catalogue', name='alpha')
    assert opened['active'] == 'alpha'
    listing = await call_tool('list_catalogues')
    assert listing['active'] == 'alpha'

    set_root(None)


async def test_open_missing_slot_raises():
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))
    try:
        await call_tool('open_catalogue', name='nonesuch')
    except Exception:
        set_root(None)
        return
    set_root(None)
    raise AssertionError('expected SlotMissing on open of absent slot')


async def test_create_existing_slot_raises():
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))
    await call_tool('create_catalogue', name='alpha')
    try:
        await call_tool('create_catalogue', name='alpha')
    except Exception:
        set_root(None)
        return
    set_root(None)
    raise AssertionError('expected SlotExists on duplicate create')


async def test_slot_tools_reject_invalid_slug():
    """The validation lives in CatalogueRoot but flows through the
    tool surface; we cover one representative attack here."""
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))
    try:
        await call_tool('create_catalogue', name='../etc/passwd')
    except Exception:
        set_root(None)
        return
    set_root(None)
    raise AssertionError('expected InvalidSlot on path-traversal slug')


async def test_writes_persist_across_open():
    """Mutate slot alpha; switch to beta; switch back to alpha;
    the prior writes are still there."""
    tmp = Path(tempfile.mkdtemp(prefix='v4cat-mcp-slot-'))
    set_root(CatalogueRoot(tmp))

    await call_tool('create_catalogue', name='alpha')
    await call_tool('introduce_break', number='F1', name='Persist test')

    await call_tool('create_catalogue', name='beta')
    breaks_in_beta = await read_resource('catalogue://breaks')
    nums_in_beta = {b['number'] for b in breaks_in_beta}
    assert 'F1' not in nums_in_beta  # beta is fresh

    await call_tool('open_catalogue', name='alpha')
    breaks_in_alpha = await read_resource('catalogue://breaks')
    nums_in_alpha = {b['number'] for b in breaks_in_alpha}
    assert 'F1' in nums_in_alpha

    set_root(None)


# -----------------------------------------------------------------------------
# Test harness
# -----------------------------------------------------------------------------

ALL_TESTS = [
    test_introduce_break_tool,
    test_introduce_object_with_lineage,
    test_introduce_object_with_attrs,
    test_witness_and_origin_derivation,
    test_chronological_origin_emerges_via_tools,
    test_refine_tool,
    test_defer_promote_via_tools,
    test_boundary_via_tool,
    test_query_wedge_tool,
    test_kquery_tool_default,
    test_kquery_tool_with_universe,
    test_tropical_min_tool_recovers_origin,
    test_tropical_min_tool_over_catalogue_order,
    test_kquery_tool_emit_subset,
    test_breaks_resource,
    test_break_detail_resource,
    test_object_detail_resource,
    test_retroactive_resource_empty_for_synthetic,
    test_axes_resource,
    test_lineage_resource_template,
    test_docs_index_lists_resources,
    test_methodology_doc_resource,
    test_theory_doc_resource,
    test_tutorial_doc_resource,
    test_examples_doc_resource,
    test_readme_doc_resource,
    test_analyze_new_processor_prompt,
    test_audit_md_vs_sql_prompt,
    test_next_processor_prompt_uses_current_catalogue,
    test_snap_to_grid_prompt,
    test_full_session_synthetic,
    # Slot mode
    test_slot_tools_error_in_pinned_mode,
    test_create_then_list_slots,
    test_open_existing_slot_switches_active,
    test_open_missing_slot_raises,
    test_create_existing_slot_raises,
    test_slot_tools_reject_invalid_slug,
    test_writes_persist_across_open,
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
