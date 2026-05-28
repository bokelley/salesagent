#!/usr/bin/env python3
"""Fast changed-file quality loop for local development.

This intentionally complements, not replaces, ``make quality``. It runs checks
that can be scoped safely to files changed against ``origin/main`` plus staged,
unstaged, and untracked files. Full mypy, duplication ratchet, and all unit
tests remain in the commit/CI gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "origin/main"
FULL_MYPY_TRIGGERS = {"mypy.ini", "pyproject.toml", "uv.lock"}
PYTEST_TRIGGERS = {"pytest.ini", "pyproject.toml"}


def run_git(args: list[str], *, check: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if result.returncode != 0:
        return ""
    return result.stdout


def run_step(label: str, command: list[str]) -> int:
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def existing(paths: list[str]) -> list[str]:
    return [path for path in paths if (REPO_ROOT / path).exists()]


def changed_files(base_ref: str) -> list[str]:
    files: set[str] = set()

    merge_base = run_git(["merge-base", base_ref, "HEAD"]).strip()
    if merge_base:
        files.update(run_git(["diff", "--name-only", "--diff-filter=ACMR", f"{merge_base}...HEAD"]).splitlines())
    else:
        print(f"Warning: could not resolve merge base for {base_ref}; using worktree changes only.", file=sys.stderr)

    files.update(run_git(["diff", "--name-only", "--diff-filter=ACMR"]).splitlines())
    files.update(run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"]).splitlines())
    files.update(run_git(["ls-files", "--others", "--exclude-standard"]).splitlines())

    return sorted(path for path in files if path)


def py_files(paths: list[str], *, prefix: str | None = None) -> list[str]:
    selected = [path for path in paths if path.endswith((".py", ".pyi"))]
    if prefix is not None:
        selected = [path for path in selected if path == prefix or path.startswith(f"{prefix}/")]
    return existing(selected)


def unit_test_files(paths: list[str]) -> list[str]:
    tests = []
    for path in paths:
        path_obj = Path(path)
        if (
            path_obj.suffix == ".py"
            and path_obj.name.startswith("test_")
            and (path.startswith("tests/unit/") or path.startswith("tests/harness/"))
        ):
            tests.append(path)
    return existing(tests)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast checks against changed files")
    parser.add_argument(
        "--base", default=DEFAULT_BASE, help=f"base ref for committed changes (default: {DEFAULT_BASE})"
    )
    parser.add_argument(
        "--full-unit", action="store_true", help="run the full unit suite instead of changed unit tests"
    )
    parser.add_argument("--with-duplication", action="store_true", help="also run the duplication ratchet")
    parser.add_argument("--no-tests", action="store_true", help="skip pytest even when unit tests changed")
    args = parser.parse_args()

    changed = changed_files(args.base)
    if not changed:
        print("No changed files detected.", flush=True)
        return 0

    print(f"Changed files: {len(changed)}", flush=True)
    for path in changed:
        print(f"  {path}", flush=True)

    failures: list[str] = []

    ruff_targets = py_files(changed)
    if ruff_targets:
        if run_step("ruff format", ["uv", "run", "ruff", "format", "--check", *ruff_targets]) != 0:
            failures.append("ruff format")
        if run_step("ruff check", ["uv", "run", "ruff", "check", *ruff_targets]) != 0:
            failures.append("ruff check")
    else:
        print("\n== ruff ==\nNo changed Python files; skipped.")

    mypy_targets = ["src/"] if FULL_MYPY_TRIGGERS & set(changed) else py_files(changed, prefix="src")
    if mypy_targets:
        if run_step("mypy", ["uv", "run", "mypy", *mypy_targets, "--config-file=mypy.ini"]) != 0:
            failures.append("mypy")
    else:
        print("\n== mypy ==\nNo changed src Python files; skipped.")

    if args.with_duplication:
        duplication_targets = [
            path for path in py_files(changed) if path.startswith("src/") or path.startswith("tests/")
        ]
        if duplication_targets:
            duplication_command = [
                "uv",
                "run",
                "python",
                ".pre-commit-hooks/check_code_duplication.py",
                *duplication_targets,
            ]
            if run_step("duplication", duplication_command) != 0:
                failures.append("duplication")
        else:
            print("\n== duplication ==\nNo changed src/tests Python files; skipped.")
    else:
        print(
            "\n== duplication ==\nSkipped for speed. Run `make quality` or add `--with-duplication` for the full ratchet."
        )

    if args.no_tests:
        print("\n== pytest ==\nSkipped by --no-tests.")
    elif args.full_unit or PYTEST_TRIGGERS & set(changed):
        if run_step("pytest", ["uv", "run", "pytest", "tests/unit/", "-x", "-q", "--tb=short"]) != 0:
            failures.append("pytest")
    else:
        tests = unit_test_files(changed)
        if tests:
            if run_step("pytest", ["uv", "run", "pytest", *tests, "-x", "-q", "--tb=short"]) != 0:
                failures.append("pytest")
        else:
            print(
                "\n== pytest ==\nNo changed unit test files; skipped. Use `--full-unit` when the change needs the full unit suite."
            )

    if failures:
        print("\nFailed steps: " + ", ".join(failures), file=sys.stderr)
        return 1

    print("\nFast quality checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
