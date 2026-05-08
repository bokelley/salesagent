"""Audit schema drift between salesagent extensions and the adcp library.

Run on every adcp version bump to catch:
  - Wire-visible extension fields that buyers can't use (because they're not in spec)
  - Internal-only fields safely excluded from serialization
  - Redundant redeclarations (same type + default as parent — copy/paste residue)
  - Pattern #1 violations (standalone classes that should extend a library type)
  - Type-mismatched overrides

Usage:
    PYTHONPATH=. uv run python scripts/audit_schema_drift.py

Exits 0 always — output is informational, intended to be diffed across runs.

The methodology is documented in PR #208 — see the umbrella issue #219 for the
deferred items this audit surfaces.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

import adcp


def get_library_parent(cls: type) -> type | None:
    """Return the name-matched library parent (per Pattern #1: ``class X(LibraryX)``)."""
    for base in cls.__mro__[1:]:
        if not base.__module__.startswith("adcp."):
            continue
        if base.__name__ == cls.__name__:
            return base
    return None


def is_excluded(field_info: Any) -> bool:
    return getattr(field_info, "exclude", None) is True


def walk_our_schema_modules():
    """Yield every Pydantic BaseModel class declared in src/core/schemas/."""
    import src.core.schemas as pkg

    seen: set[type] = set()
    for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.core.schemas."):
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            print(f"# skip {name}: {e}")
            continue
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if not inspect.isclass(obj):
                continue
            if obj in seen:
                continue
            if not hasattr(obj, "model_fields"):
                continue
            if obj.__module__.startswith("adcp."):
                continue
            if not obj.__module__.startswith("src.core.schemas"):
                continue
            seen.add(obj)
            yield obj


def audit_class(cls: type) -> dict:
    parent = get_library_parent(cls)
    if parent is None:
        return {"cls": cls, "parent": None, "skip": True}

    our_fields = set(cls.model_fields.keys())
    parent_fields = set(parent.model_fields.keys())

    overrides = our_fields & parent_fields
    extensions = our_fields - parent_fields

    wire_visible_extensions: list[str] = []
    internal_extensions: list[str] = []
    for fname in sorted(extensions):
        finfo = cls.model_fields[fname]
        if is_excluded(finfo):
            internal_extensions.append(fname)
        else:
            wire_visible_extensions.append(fname)

    # Redundant redeclarations: same type, default, required-status as parent.
    # These are copy/paste residue (no functional override).
    redundant: list[str] = []
    for fname in sorted(overrides):
        if fname not in cls.__annotations__:
            continue  # inherited, not redeclared
        ours = cls.model_fields[fname]
        par = parent.model_fields[fname]
        if (
            ours.annotation == par.annotation
            and ours.default == par.default
            and ours.is_required() == par.is_required()
            and ours.exclude == par.exclude
        ):
            redundant.append(fname)

    return {
        "cls": cls,
        "parent": parent,
        "overrides": sorted(overrides),
        "wire_visible_extensions": wire_visible_extensions,
        "internal_extensions": internal_extensions,
        "redundant_redeclarations": redundant,
        "skip": False,
    }


def main() -> int:
    print(f"# adcp library version: {adcp.__version__}")
    try:
        from adcp import get_adcp_spec_version  # type: ignore[attr-defined]

        print(f"# adcp spec version  : {get_adcp_spec_version()}")
    except Exception:
        try:
            print(f"# adcp spec version  : {adcp.get_adcp_version()}  (deprecated accessor)")
        except Exception as e:
            print(f"# adcp spec version  : unknown ({e})")
    print()

    findings = [audit_class(cls) for cls in walk_our_schema_modules()]
    findings = [f for f in findings if not f["skip"]]
    findings.sort(key=lambda f: (f["cls"].__module__, f["cls"].__name__))

    # ── Section 1: wire-visible extensions ────────────────────────────────────
    print("## WIRE-VISIBLE EXTENSIONS")
    print("# fields we add that ship to buyers and are NOT in library parent")
    print()
    any_wire = False
    for f in findings:
        if not f["wire_visible_extensions"]:
            continue
        any_wire = True
        cls, parent = f["cls"], f["parent"]
        print(f"  {cls.__module__}::{cls.__name__}  (extends {parent.__name__})")
        for field in f["wire_visible_extensions"]:
            print(f"    + {field}")
        print()
    if not any_wire:
        print("  (none)")
    print()

    # ── Section 2: internal-only extensions ───────────────────────────────────
    print("## INTERNAL-ONLY EXTENSIONS (exclude=True)")
    print("# fields we add that never reach the wire — safe but worth labeling")
    print()
    for f in findings:
        if not f["internal_extensions"]:
            continue
        cls, parent = f["cls"], f["parent"]
        print(f"  {cls.__module__}::{cls.__name__}  (extends {parent.__name__})")
        for field in f["internal_extensions"]:
            print(f"    . {field}")
        print()

    # ── Section 3: redundant redeclarations ───────────────────────────────────
    print("## REDUNDANT REDECLARATIONS")
    print("# fields redeclared with same type + default + exclude as parent — drop them")
    print()
    any_redundant = False
    for f in findings:
        if not f["redundant_redeclarations"]:
            continue
        any_redundant = True
        cls, parent = f["cls"], f["parent"]
        print(f"  {cls.__module__}::{cls.__name__}  (extends {parent.__name__})")
        for field in f["redundant_redeclarations"]:
            print(f"    ~ {field}")
        print()
    if not any_redundant:
        print("  (none)")
    print()

    # ── Section 4: type-mismatched overrides ──────────────────────────────────
    print("## TYPE-MISMATCHED OVERRIDES")
    print("# fields we redeclare with a different type than the parent — verify intent")
    print()
    for f in findings:
        type_mismatched = []
        for field in f["overrides"]:
            if field in f["redundant_redeclarations"]:
                continue
            cls = f["cls"]
            parent = f["parent"]
            our_t = cls.model_fields[field].annotation
            par_t = parent.model_fields[field].annotation
            if our_t != par_t:
                type_mismatched.append((field, par_t, our_t))
        if not type_mismatched:
            continue
        cls, parent = f["cls"], f["parent"]
        print(f"  {cls.__module__}::{cls.__name__}  (extends {parent.__name__})")
        for field, par_t, our_t in type_mismatched:
            print(f"    = {field}  type: {par_t} -> {our_t}")
        print()

    # ── Section 5: standalone classes that should follow Pattern #1 ───────────
    print("## STANDALONE CLASSES WITH LIBRARY COUNTERPART (Pattern #1 candidates)")
    print("# our class name matches a public adcp.types name, but we don't extend it")
    print()
    import adcp.types as adcp_types

    library_names = {n for n in dir(adcp_types) if not n.startswith("_")}
    standalone_with_counterpart = []
    for cls in walk_our_schema_modules():
        if cls.__name__ not in library_names:
            continue
        lib_cls = getattr(adcp_types, cls.__name__, None)
        if lib_cls is None or not inspect.isclass(lib_cls):
            continue
        if lib_cls in cls.__mro__:
            continue
        standalone_with_counterpart.append((cls, lib_cls))

    for cls, lib_cls in sorted(standalone_with_counterpart, key=lambda p: p[0].__module__):
        our_fields = set(getattr(cls, "model_fields", {}).keys())
        lib_fields = set(getattr(lib_cls, "model_fields", {}).keys())
        extra = our_fields - lib_fields
        missing = lib_fields - our_fields
        print(f"  {cls.__module__}::{cls.__name__}  (library: {lib_cls.__module__}.{lib_cls.__name__})")
        if extra:
            print(f"    we have, library doesn't: {sorted(extra)}")
        if missing:
            print(f"    library has, we don't:    {sorted(missing)}")
        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    n_wire = sum(len(f["wire_visible_extensions"]) for f in findings)
    n_int = sum(len(f["internal_extensions"]) for f in findings)
    n_red = sum(len(f["redundant_redeclarations"]) for f in findings)
    n_classes = len(findings)
    print("## SUMMARY")
    print(f"  classes audited:            {n_classes}")
    print(f"  wire-visible extensions:    {n_wire}")
    print(f"  internal-only extensions:   {n_int}")
    print(f"  redundant redeclarations:   {n_red}")
    print(f"  standalone w/ counterpart:  {len(standalone_with_counterpart)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
