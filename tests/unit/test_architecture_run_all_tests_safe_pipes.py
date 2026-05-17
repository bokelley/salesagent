"""Guard: run_all_tests.sh must not reintroduce pipefail-fragile patterns.

The pre-push hook runs `./run_all_tests.sh quick` and aborts the push on any
non-zero exit. Under `set -eo pipefail`, two patterns abort the script even
when all tests pass:

1. ``ls <glob> 2>/dev/null | tail | xargs ...`` — when the glob has no
   matches, `ls` exits non-zero, propagates through the pipe, and `set -e`
   aborts the script. Hit on fresh worktrees (no test-results subdirs) and
   on the final summary when no JSON reports were produced.

2. ``[ -z "$X" ] && echo PASSED && exit 0`` — short-circuit chains where a
   failing test command precedes the action are confusing under `set -e` on
   older bash versions and disguise the actual exit code.

Fix: wrap fragile pipes in ``{ … || true; } | …`` and use explicit
``if/else`` for the final summary. See #432.

No allowlist — zero tolerance. Reintroduces are caught here.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "run_all_tests.sh"


def _strip_comments(text: str) -> str:
    """Drop shell `#` comments so guard regexes don't fire on examples in docs."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class TestRunAllTestsSafePipes:
    """Guard against reintroducing the patterns that caused #432."""

    def test_script_exists(self):
        assert SCRIPT.exists(), f"run_all_tests.sh not found at {SCRIPT}"

    def test_no_unguarded_ls_to_tail_pipe(self):
        """`ls <glob> | tail …` without `|| true` aborts under set -eo pipefail
        when the glob has no matches. Wrap in `{ … || true; }`."""
        body = _strip_comments(SCRIPT.read_text())
        # Match `ls … | tail` where the preceding `ls` is NOT inside
        # `{ … || true; }` braces or followed by `|| true` on its own.
        offenders = []
        for match in re.finditer(r"^\s*ls\s+[^\n|]+\|\s*tail\b", body, re.MULTILINE):
            offenders.append(match.group(0).strip())
        assert not offenders, (
            "Unguarded `ls … | tail` pipeline(s) found in run_all_tests.sh:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\nWrap in `{ ls … || true; } | tail …` to survive empty globs "
            "under `set -eo pipefail`. See tests/unit/test_architecture_run_all_tests_safe_pipes.py."
        )

    def test_no_unguarded_ls_to_while_pipe(self):
        """`ls <glob> | while read …` without `|| true` is the same hazard
        — it bit the final summary line in #432."""
        body = _strip_comments(SCRIPT.read_text())
        offenders = []
        for match in re.finditer(r"^\s*ls\s+[^\n|]+\|\s*while\b", body, re.MULTILINE):
            offenders.append(match.group(0).strip())
        assert not offenders, (
            "Unguarded `ls … | while` pipeline(s) found in run_all_tests.sh:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\nWrap in `{ ls … || true; } | while …` to survive empty globs."
        )

    def test_no_test_command_short_circuit_to_state_mutation(self):
        """`[ … ] && X` chains where X is a state mutation (assignment, cp,
        mv, rm) collapse the test command's exit code into the action, which
        under `set -e` on older bash can disguise the actual reason for
        script failure. Use explicit `if [ … ]; then X; fi` instead.

        Covers both the FAILURES= short-circuits and the `[ -f ] && cp`
        pattern inside `collect_reports` that surfaced in PR #454 review."""
        body = _strip_comments(SCRIPT.read_text())
        offenders = re.findall(
            r"^\s*\[[^\n]+\]\s*&&\s*(?:FAILURES=|cp\s|mv\s|rm\s)",
            body,
            re.MULTILINE,
        )
        assert not offenders, (
            "Short-circuit `[ … ] && (FAILURES=|cp|mv|rm) …` found in run_all_tests.sh:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\nUse explicit `if [ … ]; then …; fi`."
        )

    def test_no_summary_short_circuit(self):
        """The final summary block must not use `[ -z $FAILURES ] && echo … && exit 0`
        — that pattern misreports security-audit failures as test failures on
        older bash and obscures the real exit code."""
        body = _strip_comments(SCRIPT.read_text())
        offenders = re.findall(
            r"^\s*\[\s*-z\s+\"\$\{?FAILURES[^\n]*\]\s*&&\s*echo[^\n]*&&\s*exit",
            body,
            re.MULTILINE,
        )
        assert not offenders, (
            "Short-circuit final summary found in run_all_tests.sh:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + '\n\nUse explicit `if [ -z "$FAILURES" ]; then ...; fi`.'
        )
