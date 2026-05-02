"""
v4cat.mcp_server — MCP server exposing the ISA to LLM clients.

Wraps v4cat.SymmetryCatalogue as Model Context Protocol tools,
resources, and prompts. Per methodology.md's MCP interface section:

  * Tools: each ISA verb is one tool (introduce_break,
    introduce_object, witness, refine, defer, promote, boundary).
  * Resources: addressable URIs for derived views
    (catalogue://breaks, catalogue://retroactive, etc.).
  * Prompts: workflow templates (analyze_new_object,
    audit_md_vs_sql, next_object, snap_to_grid_check).

Run via stdio (the MCP standard for local servers)::

    python -m v4cat.mcp_server [--db PATH | --root DIR [--default SLOT]]

Two persistence modes:

  * ``--db PATH`` (pinned-file): server pinned to one SQLite file.
    No slot tools available; the LLM cannot redirect the target.
  * ``--root DIR`` (named-slot): server confined to a sandbox
    directory. Slot tools (``list_catalogues``, ``open_catalogue``,
    ``create_catalogue``) accept slug names only; the file path is
    derived by the server. See :mod:`v4cat.sandbox` for the
    validation rules.

Or import the ``server`` instance for in-process testing.
"""
from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .catalogue import SymmetryCatalogue
from .sandbox import CatalogueRoot, InvalidSlot, SlotExists, SlotMissing
from .views import (
    ALL_CELLS,
    agent_level_witnesses as _agent_witnesses,
    axis_distribution as _axis_distribution,
    consistency as _consistency,
    kquery as _kquery,
    mixed_breaks as _mixed_breaks,
    retroactive_attributions as _retroactive,
    spec_axis_summary as _spec_axis_summary,
    top_originators as _top_originators,
    wedge as _wedge,
)


# -----------------------------------------------------------------------------
# Catalogue lifecycle — one active SymmetryCatalogue per server
# -----------------------------------------------------------------------------

DEFAULT_DB_PATH = os.environ.get(
    'CATALOGUE_DB',
    str(Path(__file__).parent.parent / 'catalogue.db'),
)

_cat: Optional[SymmetryCatalogue] = None
_root: Optional[CatalogueRoot] = None
_active_slot: Optional[str] = None


def get_catalogue() -> SymmetryCatalogue:
    """Lazy singleton. The first call opens (and bootstraps) the DB.

    In pinned-file mode this returns the one configured catalogue;
    in named-slot mode it returns whichever slot was last opened
    (or the ``--default`` slot if one was given at startup).

    If neither has been configured, falls back to
    :data:`DEFAULT_DB_PATH` — preserved for in-process tests and
    legacy invocations without args.
    """
    global _cat
    if _cat is None:
        _cat = SymmetryCatalogue(DEFAULT_DB_PATH)
    return _cat


def set_catalogue(cat: SymmetryCatalogue) -> None:
    """Used by tests to inject an in-memory catalogue."""
    global _cat
    _cat = cat


def set_root(root: Optional[CatalogueRoot]) -> None:
    """Used by tests to install or clear the slot-mode root."""
    global _root, _active_slot
    _root = root
    if root is None:
        _active_slot = None


def _require_root() -> CatalogueRoot:
    """Raise if the server isn't in named-slot mode."""
    if _root is None:
        raise RuntimeError(
            "server was started without --root; slot tools are "
            "unavailable in pinned-file mode"
        )
    return _root


def _swap_active(cat: SymmetryCatalogue, slot: str) -> None:
    """Replace the active catalogue, closing the old one."""
    global _cat, _active_slot
    if _cat is not None:
        try:
            _cat.close()
        except Exception:
            pass
    _cat = cat
    _active_slot = slot


def _commit_after(fn):
    """Wrap a tool function so its mutation is committed atomically.

    Uses functools.wraps so FastMCP's signature introspection
    follows __wrapped__ to the original function — otherwise the
    wrapper's ``(*args, **kwargs)`` signature would be exposed as
    the tool's parameter list.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        get_catalogue().commit()
        return result
    return wrapper


# -----------------------------------------------------------------------------
# FastMCP server
# -----------------------------------------------------------------------------

server = FastMCP(
    name='symmetry-catalogue',
    instructions="""
The symmetry-break catalogue is a queryable graph of breaks (named
structural distinctions), specs (witness objects of any kind a domain
extension introduces), and witnesses (typed edges between specs and
breaks).

Use the ISA verbs (tools) to extend the catalogue:
- introduce_break / introduce_object / introduce_tension to add nodes
- witness to record a (spec, break, kind) edge
- refine to annotate a (break, spec) edge with a named refinement
- defer / promote / boundary for lifecycle states

Read views (resources) to explore the graph:
- catalogue://breaks lists all breaks
- catalogue://breaks/{number} returns one break with witnesses
- catalogue://retroactive lists breaks whose chronological origin
  precedes their catalogue introduction
- catalogue://violations/{rule} runs a domain-extension consistency
  rule (the rule maps to a `<rule>_violations` view that the
  domain extension's schema must define)

Use prompts to scaffold common workflows.

Methodological commitments (see methodology.md):
- No element is foundational; identity is constituted through
  witnessed traces.
- Time and lineage are breaks-themselves; attribution is always
  derived via tropical MIN-year over origin-class edges.
- The catalogue thickens forward; prior readings aren't corrected,
  they're re-derived as the trace-set grows.
""",
)


# =============================================================================
# TOOLS — the ISA verbs
# =============================================================================

@server.tool()
@_commit_after
def introduce_break(
    number: str,
    name: str,
    short_desc: Optional[str] = None,
    axes: Optional[list[str]] = None,
) -> dict:
    """ISA: INTRODUCE break <number>.

    Add a new symmetry break to the catalogue. Idempotent on
    ``number``. Optionally tag the break with one or more axes
    (spatial / temporal / parallel / equivalential / eventual / meta).
    """
    get_catalogue().introduce_break(
        number, name, short_desc=short_desc, axes=axes,
    )
    return {'ok': True, 'number': number}


@server.tool()
@_commit_after
def introduce_object(
    id: str,
    name: str,
    year: Optional[int] = None,
    catalogue_order: Optional[int] = None,
    notes: Optional[str] = None,
    lineage: Optional[list[list[str]]] = None,
    attrs: Optional[dict] = None,
) -> dict:
    """ISA: INTRODUCE object <id>.

    Add a new witness-object — any structured artefact a domain
    extension wants to catalogue (programming language, processor,
    cryptographic primitive, file system, formal system,
    schema-version, etc.).

    Framework-minimal signature. Only attributes that are
    load-bearing for framework views are first-class:

      * ``year`` — tropical-MIN axis for break_origin
      * ``catalogue_order`` — tropical-MIN axis for break_first_seen
        (defaults to next available integer if omitted)
      * ``notes`` — free-form annotation
      * ``lineage`` — list of ``[ancestor_id, kind]`` pairs;
        populates the lineages table for inheritance queries

    Domain-specific attributes (any name/value pairs the domain
    schema wants to track) go in ``attrs``: a dict whose entries
    are stored as (spec_id, name, value) rows in spec_attributes.
    Values are stringified; cast at query time.

    Example (synthetic):

        introduce_object(
            id='alpha', name='Alpha',
            year=1980,
            attrs={'role': 'origin', 'kind': 'synthetic'},
        )
    """
    lineage_pairs = [tuple(p) for p in lineage] if lineage else None
    get_catalogue().introduce_object(
        id, name,
        year=year,
        catalogue_order=catalogue_order, notes=notes,
        lineage=lineage_pairs,
        attrs=attrs,
    )
    return {'ok': True, 'id': id}


@server.tool()
@_commit_after
def introduce_tension(
    id: str,
    name: str,
    description: Optional[str] = None,
    status: str = 'open',
    addressing_stage: Optional[str] = None,
    breaks_involved: Optional[list[str]] = None,
) -> dict:
    """ISA: INTRODUCE tension <id>.

    Add a structural tension — a concern about implementation
    alignment with the metamodel (e.g., T1-T5 in symmetries.md).
    Tensions aren't witnesses; they're meta-claims that may
    eventually motivate schema breaks.
    """
    get_catalogue().introduce_tension(
        id, name,
        description=description,
        status=status,
        addressing_stage=addressing_stage,
        breaks_involved=breaks_involved,
    )
    return {'ok': True, 'id': id}


@server.tool()
@_commit_after
def witness(
    subject: str,
    break_number: str,
    kind: str,
    notes: Optional[str] = None,
    scope: str = 'spec',
) -> dict:
    """ISA: WITNESS <subject> <break> <kind>.

    Record a contribution edge from a spec (subject) to a break.
    ``kind`` is one of: origin, catalogue-introduces, confirms,
    refines, first-witness, precedes, cross-vendor, inherits,
    deferred-candidate, sibling-boundary, gates-with-fault.

    ``scope`` is 'spec' (default) or 'agent' (for sub-spec scopes
    when one named witness object actually contains multiple
    distinguishable contributors and a break is properly attributed
    to one of them).
    """
    get_catalogue().witness(
        subject, break_number, kind, notes=notes, scope=scope,
    )
    return {'ok': True, 'subject': subject, 'break': break_number, 'kind': kind}


@server.tool()
@_commit_after
def refine(
    break_number: str,
    object_id: str,
    name: str,
    description: Optional[str] = None,
) -> dict:
    """ISA: REFINE <break> <object> <name>.

    Annotate an (object, break) edge with a named refinement.
    Multiple refinements per edge admitted. Use after a
    ``witness`` of kind=refines to record what specifically was
    refined.
    """
    get_catalogue().refine(
        break_number, object_id, name, description=description,
    )
    return {'ok': True, 'break': break_number, 'object': object_id, 'name': name}


@server.tool()
@_commit_after
def defer(break_number: str, by: str, reason: Optional[str] = None) -> dict:
    """ISA: DEFER <break>.

    Mark a break as a deferred candidate: named at <by> but not yet
    structurally adopted. Status becomes 'deferred' via the derived
    view until a confirms witness from a different spec arrives.
    """
    get_catalogue().defer(break_number, by=by, reason=reason)
    return {'ok': True, 'break': break_number, 'status': 'deferred'}


@server.tool()
@_commit_after
def promote(break_number: str, by: str, reason: Optional[str] = None) -> dict:
    """ISA: PROMOTE <break>.

    Promote a deferred break to active. Records a confirms witness
    from <by> (which should be a different spec from the original
    deferred-candidate spec).
    """
    get_catalogue().promote(break_number, by=by, reason=reason)
    return {'ok': True, 'break': break_number}


@server.tool()
@_commit_after
def boundary(break_number: str, reason: str, by: str) -> dict:
    """ISA: BOUNDARY <break>.

    Mark a break as a deliberate metamodel non-extension. Q81 is
    the canonical example: multi-CPU systems are handled via
    sibling-framework composition rather than by extending the
    metamodel.
    """
    get_catalogue().boundary(break_number, reason, by=by)
    return {'ok': True, 'break': break_number, 'status': 'sibling-boundary'}


# -----------------------------------------------------------------------------
# Slot management (named-slot mode only — error in pinned-file mode)
# -----------------------------------------------------------------------------

@server.tool()
def list_catalogues() -> dict:
    """List slot names available under the configured ``--root``.

    Only available in named-slot mode (server started with
    ``--root DIR``). Returns ``{'slots': [...], 'active': <name>}``;
    ``active`` is the slot currently being mutated, or null if no
    slot has been opened yet.
    """
    return {
        'slots':  _require_root().list_slots(),
        'active': _active_slot,
    }


@server.tool()
def open_catalogue(name: str) -> dict:
    """Switch the active catalogue to slot ``name``.

    Only available in named-slot mode. The slot must already
    exist (use ``create_catalogue`` to make a new one). Slot
    name must match ``[A-Za-z0-9][A-Za-z0-9_-]{0,63}``.
    """
    path = _require_root().path_for(name, must_exist=True)
    cat = SymmetryCatalogue(path)
    _swap_active(cat, name)
    return {'ok': True, 'active': name}


@server.tool()
def create_catalogue(name: str) -> dict:
    """Create a new slot ``name`` and switch to it.

    Only available in named-slot mode. Errors if the slot already
    exists; use ``open_catalogue`` for that case. Slot name must
    match ``[A-Za-z0-9][A-Za-z0-9_-]{0,63}``.

    On success the new slot is bootstrapped (framework schema
    loaded; closure check runs) and becomes the active catalogue.
    """
    root = _require_root()
    if root.exists(name):
        raise SlotExists(name)
    path = root.path_for(name)
    cat = SymmetryCatalogue(path)
    _swap_active(cat, name)
    return {'ok': True, 'active': name}


# -----------------------------------------------------------------------------
# Analytic queries (read-only tools — useful when an LLM wants a
# parameterised query rather than a fixed-URI resource)
# -----------------------------------------------------------------------------

@server.tool()
def query_origin(
    break_number: str,
    axis_column: str = 'year',
) -> dict | None:
    """Return the originator of a break — tropical MIN over a chosen
    metric field, restricted to origin-class witness edges. Default
    axis is ``year``; any ordered column on ``specs`` works."""
    return get_catalogue().origin(break_number, axis_column=axis_column)


@server.tool()
def query_first_seen(break_number: str) -> dict | None:
    """Return the spec where the catalogue first analysed a break
    (tropical MIN-catalogue_order over catalogue-introduces edges).
    ``catalogue_order`` is the catalogue's own exposition axis, not
    a domain axis, so this query is not parametric."""
    return get_catalogue().first_seen(break_number)


@server.tool()
def query_status(break_number: str) -> str | None:
    """Return derived status: active / deferred / sibling-boundary."""
    return get_catalogue().status(break_number)


@server.tool()
def query_lineage(object_id: str) -> list[dict]:
    """Return the ancestor chain for an object via the lineages
    table. Rows ordered by depth (1 = direct parent)."""
    return get_catalogue().lineage(object_id)


@server.tool()
def query_inherited_breaks(object_id: str) -> list[dict]:
    """Return breaks that <object_id> inherits via its lineage chain
    (any ancestor's origin / catalogue-introduces witnesses)."""
    return get_catalogue().inherited_breaks(object_id)


@server.tool()
def kquery(
    set_a: list[str],
    set_b: list[str],
    universe: Optional[list[str]] = None,
    emit: Optional[list[str]] = None,
) -> dict:
    """The Klein-four read primitive — a classifier labelling every
    element of ``universe`` with its (in A, in B) membership signature.

    Returns the four-cell partition::

        {
          '11': items in A and in B (agreement),
          '10': items in A but not B (left residue),
          '01': items in B but not A (right residue),
          '00': items in universe absent from both (shared blindness)
        }

    Defaults: ``universe = A ∪ B`` (collapses cell ``00`` to empty);
    ``emit`` = all four cells.

    Every other read in the catalogue is a named selection from this
    classifier. Examples:

      * Wedge audit (drift between two sources):
        kquery(prose_breaks, sql_breaks, emit=['10', '01'])
      * Agreement (intersection):
        kquery(a, b, emit=['11'])
      * Coverage (union over universe):
        kquery(a, b, emit=['10', '01', '11'])
      * Shared blindness (the methodologically significant cell):
        kquery(a, b, universe=full_universe, emit=['00'])
    """
    cells_to_emit = tuple(emit) if emit else ALL_CELLS
    return _kquery(
        set_a, set_b,
        universe=universe,
        emit=cells_to_emit,
    )


@server.tool()
def query_wedge(set_a: list[str], set_b: list[str]) -> dict:
    """Wedge-product audit (sugar over ``kquery`` emitting cells
    10 and 01 plus 11 for context). Returns the legacy shape with
    ``in_a_not_b`` / ``in_b_not_a`` / ``in_both`` keys.

    For new code prefer ``kquery``, which exposes all four cells
    and admits a bounded universe for shared-blindness analysis.
    """
    return _wedge(set_a, set_b)


@server.tool()
def tropical_min(
    axis_column: str,
    witness_kinds: list[str],
    break_number: Optional[str] = None,
    direction: str = 'min',
) -> list[dict]:
    """The framework's generic tropical-query operator.

    For each break (or just ``break_number`` if specified), find
    the spec(s) where ``axis_column`` is at its extremum among
    witnesses with the given kinds.

    The framework's ``query_origin`` and ``query_first_seen`` are
    concrete instances:

      * Originator query (year axis, origin-class kinds):
        ``tropical_min(axis_column='year',
                       witness_kinds=['origin', 'catalogue-introduces'])``
      * First-seen query (catalogue_order axis, catalogue-
        introduces only):
        ``tropical_min(axis_column='catalogue_order',
                       witness_kinds=['catalogue-introduces'])``

    Domains can add their own ordered columns via ``ALTER TABLE
    specs ADD COLUMN ...`` and use this operator over them. The
    framework's commitment is to tropical aggregates over ordered
    columns; year and catalogue_order are two canonical examples,
    not the only admissible axes.

    ``direction`` is ``'min'`` (default) or ``'max'``.
    """
    return get_catalogue().tropical_min(
        axis_column=axis_column,
        witness_kinds=witness_kinds,
        break_=break_number,
        direction=direction,
    )


# =============================================================================
# RESOURCES — addressable derived views
# =============================================================================

@server.resource('catalogue://breaks')
def list_breaks() -> str:
    """All breaks with their derived origin / first-seen / status."""
    return json.dumps(get_catalogue().all_breaks(), indent=2)


@server.resource('catalogue://breaks/{number}')
def get_break(number: str) -> str:
    """One break with its witnesses + refinements."""
    cat = get_catalogue()
    breaks = [b for b in cat.all_breaks() if b['number'] == number]
    if not breaks:
        return json.dumps({'error': f'break {number!r} not found'})
    result = breaks[0]
    result['witnesses']   = cat.witnesses_for_break(number)
    result['refinements'] = cat.refinements_for_break(number)
    return json.dumps(result, indent=2, default=str)


@server.resource('catalogue://objects')
def list_objects() -> str:
    """All specs (witness objects) ordered by catalogue_order."""
    return json.dumps(get_catalogue().all_objects(), indent=2)


@server.resource('catalogue://objects/{id}')
def get_object(id: str) -> str:
    """One spec with its witnesses + lineage + domain attributes."""
    cat = get_catalogue()
    objects = [o for o in cat.all_objects() if o['id'] == id]
    if not objects:
        return json.dumps({'error': f'object {id!r} not found'})
    result = objects[0]
    result['witnesses']        = cat.witnesses_for_object(id)
    result['lineage']          = cat.lineage(id)
    result['inherited_breaks'] = cat.inherited_breaks(id)
    result['attributes']       = cat.attributes_for_object(id)
    return json.dumps(result, indent=2, default=str)


@server.resource('catalogue://retroactive')
def retroactive_view() -> str:
    """Breaks whose chronological origin precedes their catalogue
    introduction (positive retroactive_gap_years)."""
    return json.dumps(_retroactive(get_catalogue()), indent=2)


@server.resource('catalogue://tensions')
def tensions_view() -> str:
    """Open structural tensions (T1-T5 in symmetries.md)."""
    return json.dumps(
        get_catalogue().query("SELECT * FROM tensions ORDER BY id"),
        indent=2,
    )


@server.resource('catalogue://violations/{rule}')
def violations_view(rule: str) -> str:
    """Run a domain-extension consistency rule. The rule name maps
    to a ``<rule>_violations`` view that the loaded domain
    extension must define. Empty result means consistent.

    The framework ships no built-in rules; this resource is the
    addressable entry point for any rule the user's extension
    introduces.
    """
    try:
        return json.dumps(_consistency(get_catalogue(), rule), indent=2)
    except ValueError as e:
        return json.dumps({'error': str(e)})


@server.resource('catalogue://axes')
def axes_view() -> str:
    """Distribution of breaks across axes."""
    return json.dumps(_axis_distribution(get_catalogue()), indent=2)


@server.resource('catalogue://mixed_breaks')
def mixed_breaks_view() -> str:
    """Breaks committing to multiple axes."""
    return json.dumps(_mixed_breaks(get_catalogue()), indent=2)


@server.resource('catalogue://agent_witnesses')
def agent_witnesses_view() -> str:
    """Witnesses whose ``scope`` is finer than the spec — used
    when a single named witness object contains multiple
    distinguishable contributors."""
    return json.dumps(_agent_witnesses(get_catalogue()), indent=2)


@server.resource('catalogue://spec_axes')
def spec_axes_view() -> str:
    """Per-spec 5-axis declarations."""
    return json.dumps(_spec_axis_summary(get_catalogue()), indent=2)


@server.resource('catalogue://top_originators')
def top_originators_view() -> str:
    """Specs ranked by number of breaks they originated."""
    return json.dumps(_top_originators(get_catalogue(), limit=20), indent=2)


@server.resource('catalogue://lineages/{id}')
def lineage_view(id: str) -> str:
    """Ancestor chain for one object."""
    return json.dumps(get_catalogue().lineage(id), indent=2)


@server.resource('catalogue://self_hosting',
                 name='self_hosting',
                 description='Closure check status (Theorem 14.5): is the framework self-hosted at scope?')
def self_hosting_view() -> str:
    """Closure check status (Theorem 14.5).

    Returns the four-cell partition the closure check produces,
    plus pass/fail status, supported-scope kinds, and the universe
    size. Useful for any MCP client wanting to inspect whether the
    framework's implementation and catalogue agree at the
    declared scope, without forcing a hard failure.

    Schema of the returned JSON object::

        {
          "supported": true | false,         # Q-supported-claims present?
          "passing":   true | false | null,  # gap empty? null = no scope
          "scope": {
            "kinds":         [...],          # supported_kinds set
            "universe_size": <int>,
          },
          "cells": {
            "11": [<break-numbers in IMPL ∩ CAT>],   # honest agreement
            "10": [<break-numbers in IMPL \\ CAT>],   # implicit (todo)
            "01": [<break-numbers in CAT \\ IMPL>],   # promissory (todo)
            "00": [<break-numbers in neither>]        # always empty since
                                                     # universe = IMPL ∪ CAT
          },
          "todo": {
            "implicit":   [...],   # cells to catalogue (gap.10)
            "promissory": [...]    # cells to implement (gap.01)
          }
        }

    When ``passing`` is False, the ``todo`` lists are
    Corollary 14.5.1's constructive content — the precise to-do
    list to restore self-hosting at scope.
    """
    from .bootstrap import closure_status, supported_kinds
    cat = get_catalogue()
    kinds = sorted(supported_kinds(cat))
    result = closure_status(cat)
    if result is None:
        return json.dumps({
            'supported': False,
            'passing':   None,
            'scope': {'kinds': kinds, 'universe_size': 0},
            'cells':  {'00': [], '01': [], '10': [], '11': []},
            'todo':   {'implicit': [], 'promissory': []},
            'note': (
                'Catalogue is not framework-self-hosting: '
                'Q-supported-claims absent. Open with '
                'check_self_hosting=True to enable.'
            ),
        }, indent=2)
    universe_size = sum(len(result[c]) for c in ('00', '01', '10', '11'))
    return json.dumps({
        'supported': True,
        'passing':   not (result['10'] or result['01']),
        'scope': {'kinds': kinds, 'universe_size': universe_size},
        'cells': {
            '11': sorted(result['11']),
            '10': sorted(result['10']),
            '01': sorted(result['01']),
            '00': sorted(result['00']),
        },
        'todo': {
            'implicit':   sorted(result['10']),
            'promissory': sorted(result['01']),
        },
    }, indent=2)


# -----------------------------------------------------------------------------
# Documentation resources — the framework's theory, methodology,
# tutorial, examples, and README. Exposed so any MCP client (LLM or
# human) can read the design and walkthrough at runtime.
# -----------------------------------------------------------------------------

DOC_DIR = Path(__file__).parent


@server.resource('catalogue://methodology',
                 name='methodology',
                 description='Operational design — ISA, schema, KQUERY, MCP interface')
def doc_methodology() -> str:
    return (DOC_DIR / 'methodology.md').read_text()


@server.resource('catalogue://theory',
                 name='theory',
                 description='Foundations — shadow architecture, Klein-four, Yoneda+Derrida, magma+pointfree')
def doc_theory() -> str:
    return (DOC_DIR / 'theory.md').read_text()


@server.resource('catalogue://tutorial',
                 name='tutorial',
                 description='LLM-friendly walk-through — empty catalogue to small worked domain')
def doc_tutorial() -> str:
    return (DOC_DIR / 'tutorial.md').read_text()


@server.resource('catalogue://examples',
                 name='examples',
                 description='Domain templates — programming languages, crypto, databases, file systems, math, etc.')
def doc_examples() -> str:
    return (DOC_DIR / 'examples.md').read_text()


@server.resource('catalogue://readme',
                 name='readme',
                 description='Quick-start, layout, methodology summary')
def doc_readme() -> str:
    return (DOC_DIR / 'README.md').read_text()


@server.resource('catalogue://mcp_setup',
                 name='mcp_setup',
                 description='How to wire the v4cat MCP server into VS Code, Claude Desktop, Claude Code, Codex CLI')
def doc_mcp_setup() -> str:
    return (DOC_DIR / 'mcp_setup.md').read_text()


@server.resource('catalogue://docs',
                 name='docs',
                 description='Index of available documentation resources')
def doc_index() -> str:
    """List of all documentation resources with descriptions.

    Use this as the entry point when first encountering the
    framework — it tells you what to read next.
    """
    lines = ['# catalogue documentation\n']
    lines.append('Read these in roughly this order:\n')
    lines.append(
        '1. **catalogue://readme** — quick-start (you may already have this).\n'
        '2. **catalogue://mcp_setup** — how to wire the server into '
        'VS Code, Claude Desktop, Claude Code, Codex CLI.\n'
        '3. **catalogue://tutorial** — operational walk-through; '
        'most useful first read for an LLM extending the catalogue.\n'
        '4. **catalogue://methodology** — full design: ISA, schema '
        'breaks, KQUERY read primitive, MCP interface.\n'
        '5. **catalogue://theory** — foundations: shadow architecture, '
        'temporal axis, Klein-four, Yoneda+Derrida, magma+pointfree, '
        'recursive schema, convergence, trace-thickening.\n'
        '6. **catalogue://examples** — domain templates '
        '(programming languages, processors, crypto, databases, '
        'file systems, math structures, OS design, ML architectures).\n\n'
    )
    lines.append('## Data resources\n')
    lines.append(
        '* **catalogue://breaks** — all breaks with derived '
        'origin / first-seen / status\n'
        '* **catalogue://breaks/{number}** — one break with '
        'witnesses + refinements\n'
        '* **catalogue://objects** — all specs (witness objects)\n'
        '* **catalogue://objects/{id}** — one spec with '
        'witnesses + lineage + inherited breaks\n'
        '* **catalogue://retroactive** — breaks where origin precedes '
        'catalogue introduction\n'
        '* **catalogue://tensions** — open structural tensions\n'
        '* **catalogue://violations/{rule}** — domain-extension '
        'consistency rule (rule must be a valid identifier; maps to '
        'a `<rule>_violations` view defined by the loaded extension)\n'
        '* **catalogue://axes** — distribution of breaks per axis\n'
        '* **catalogue://mixed_breaks** — breaks committing to '
        'multiple axes\n'
        '* **catalogue://agent_witnesses** — witnesses scoped finer '
        'than the spec\n'
        '* **catalogue://spec_axes** — per-spec 5-axis declarations\n'
        '* **catalogue://top_originators** — most prolific originator '
        'specs\n'
        '* **catalogue://lineages/{id}** — ancestor chain for one '
        'object\n'
        '* **catalogue://self_hosting** — closure check status '
        '(Theorem 14.5); pass/fail, four-cell partition, todo list\n\n'
    )
    lines.append('## ISA verbs (mutation tools)\n')
    lines.append(
        '* `introduce_break(number, name, axes, ...)`\n'
        '* `introduce_object(id, name, year, lineage, ...)`\n'
        '* `introduce_tension(id, name, ...)`\n'
        '* `witness(subject, break_number, kind, ...)`\n'
        '* `refine(break_number, object_id, name, ...)`\n'
        '* `defer(break_number, by, reason)` / '
        '`promote(break_number, by)` / '
        '`boundary(break_number, reason, by)`\n\n'
    )
    lines.append('## Read tools\n')
    lines.append(
        '* `kquery(set_a, set_b, universe, emit)` — the Klein-four '
        'classifier (the only primitive read)\n'
        '* `query_origin / query_first_seen / query_status / '
        'query_lineage / query_inherited_breaks` — sugar over '
        'specific kquery selections + tropical aggregates\n'
        '* `query_wedge(set_a, set_b)` — legacy sugar; prefer kquery\n'
    )
    return '\n'.join(lines)


# =============================================================================
# PROMPTS — workflow templates
# =============================================================================

@server.prompt()
def analyze_new_object(spec_doc_url: str) -> str:
    """Template: examine a witness-object's spec doc and propose ISA ops."""
    return f"""Examine the spec document at {spec_doc_url}. For each structural
section, identify:

  1. Existing breaks this object inherits unchanged (witness with
     kind='inherits' or 'confirms')
  2. Refinements of existing breaks (witness with kind='refines',
     plus a refine() call recording what specifically was refined)
  3. New breaks this object forces. Number each new break in the
     catalogue's break-numbering convention; provide a
     (partition, preservation-theorem) pair for break_invariants.

Cross-check against existing catalogue:
  - Read catalogue://breaks for the current set of named breaks
  - Read catalogue://top_originators to see if this lineage already
    has originators
  - Read catalogue://lineages/{{ancestor_id}} if this object descends
    from an existing one

Output a sequence of ISA tool calls (introduce_object,
introduce_break, witness, refine) to record the analysis. Don't
forget the year and lineage edges — those are how chronological
attribution emerges automatically.

Methodological reminders:
  - No RETRO verb. If this object's chronological priority over
    an existing break is news, just add an origin witness with
    the year attribute; the view will re-derive the originator.
  - Schema breaks are additive. If the analysis surfaces a new
    structural primitive that doesn't fit existing primitives,
    flag it as a candidate for introduce_break with a documented
    (partition, preservation-theorem) pair.
"""


@server.prompt()
def audit_md_vs_sql(md_path: str, sql_path: str) -> str:
    """Template: wedge-product audit between prose and structured forms."""
    return f"""Run a wedge-product audit between {md_path} (prose) and {sql_path}
(structured). Both should encode the same content; drift between
them is information.

Steps:

  1. Extract every named break, spec, witness, and refinement from
     {md_path}. (Look for Z-numbers, Q-numbers, BF-* and LC-* labels;
     prose phrasings like "originates Q…" or "first witness of Q…".)
  2. Read catalogue://breaks and catalogue://objects to get the
     SQL-side inventory.
  3. Use query_wedge to compute the symmetric set difference.
  4. For each item in {md_path} not in {sql_path}: propose an ISA
     tool call to add it.
  5. For each item in {sql_path} not in {md_path}: flag it for prose
     attention or confirm the SQL is canonical.

Output a structured drift report and a list of suggested tool
calls. The user decides which to apply.
"""


@server.prompt()
def next_object(domain: str = 'object') -> str:
    """Template: suggest the next witness-object to analyse."""
    cat = get_catalogue()
    recent = cat.query(
        "SELECT id, name, year FROM specs "
        "WHERE catalogue_order IS NOT NULL "
        "ORDER BY catalogue_order DESC LIMIT 3"
    )
    recent_str = ', '.join(
        f"{r['name']} ({r['year']})" for r in recent
    )
    top = _top_originators(cat, limit=5)
    top_str = ', '.join(
        f"{r['spec_name']} ({r['breaks_originated']})" for r in top
    )

    return f"""Suggest the next {domain} to analyse for the catalogue.

Recent additions (last 3 by exposition order): {recent_str}

Top originators by break count: {top_str}

Heuristics for choosing:
  - An object that confirms an existing break from a different
    lineage (cross-confirmation strengthens the break's status)
  - An object likely to force a new break or significant refinement
    (richer structure, novel feature)
  - An object that fills a chronological gap or completes a family
    lineage already partially in the catalogue
  - An object outside the dominant domain of existing entries —
    something that stress-tests the metamodel from an extreme

Provide:
  1. Your suggested next {domain} (one sentence)
  2. Predicted contributions: which breaks confirmed, refined,
     possibly forced
  3. Open question: which catalogue gap does adding it close?
"""


@server.prompt()
def snap_to_grid_check(deliverable_description: str) -> str:
    """Template: check if catalogue's current entailment matches a goal."""
    return f"""Compare the catalogue's current entailment against the requested
deliverable: {deliverable_description}

Steps:

  1. Read catalogue://breaks for the active break set.
  2. Read catalogue://retroactive to see where chronology diverges
     from exposition.
  3. Read catalogue://tensions to see what's not yet aligned with
     the metamodel.
  4. Read catalogue://violations/{{rule}} for any consistency rules
     the loaded domain extension defines (skip if none).

Report one of:
  - 'consistent' — the catalogue's entailment matches the deliverable
    description (snap-to-grid achieved)
  - 'generalisation' — the catalogue entails MORE than requested;
    the deliverable is satisfied as a special case
  - 'specialisation' — the catalogue entails LESS than requested;
    name what's missing
  - 'drift' — the catalogue's entailment conflicts with the
    deliverable; surface the conflict explicitly

If gap or drift, propose ISA tool calls to close it (or argue
that the deliverable should be revised).
"""


# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    """Run the MCP server over stdio (the standard MCP transport).

    Two persistence modes are mutually exclusive:

      * ``--db PATH``: pin the server to one SQLite file. Slot
        tools error; the LLM has no string to redirect.
      * ``--root DIR``: confine the server to a sandbox directory.
        Slot tools become available; clients address catalogues by
        slug (validated by :class:`v4cat.sandbox.CatalogueRoot`).
        ``--default SLOT`` opens (or creates) one slot at startup.

    With neither flag the server falls back to
    :data:`DEFAULT_DB_PATH` for backward compatibility.
    """
    import argparse
    parser = argparse.ArgumentParser(prog='v4cat.mcp_server')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--db', type=Path, default=None,
        help='Pin server to one SQLite file (legacy single-file mode).',
    )
    mode.add_argument(
        '--root', type=Path, default=None,
        help='Sandbox root for named-slot persistence. Slot tools '
             'become available; the server only opens files under '
             'this directory matching <slug>.db.',
    )
    parser.add_argument(
        '--default', type=str, default=None,
        help='In --root mode, slot to open (or create) at startup. '
             'Slug rules apply.',
    )
    args = parser.parse_args()

    if args.default and not args.root:
        parser.error('--default requires --root')

    global DEFAULT_DB_PATH
    if args.root:
        set_root(CatalogueRoot(args.root))
        if args.default:
            root = _require_root()
            path = root.path_for(args.default)
            cat = SymmetryCatalogue(path)
            _swap_active(cat, args.default)
    elif args.db:
        DEFAULT_DB_PATH = str(args.db)

    server.run('stdio')


if __name__ == '__main__':
    main()
