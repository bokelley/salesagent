"""Guards for Python formatting and linting tool configuration."""

import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PRE_COMMIT_CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"
TEST_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "test.yml"


def test_ruff_is_the_only_first_party_formatter():
    """Formatting should be owned by Ruff, not split between Ruff and Black."""
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]
    pre_commit = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))

    assert "black" not in pyproject.get("tool", {})
    assert not any(dep.split(">", maxsplit=1)[0].split("=", maxsplit=1)[0] == "black" for dep in dev_deps)
    assert all(repo["repo"] != "https://github.com/psf/black" for repo in pre_commit["repos"])

    ruff_repo = next(
        repo for repo in pre_commit["repos"] if repo["repo"] == "https://github.com/astral-sh/ruff-pre-commit"
    )
    hook_ids = [hook["id"] for hook in ruff_repo["hooks"]]
    assert hook_ids == ["ruff", "ruff-format"]


def test_ci_enforces_ruff_lint_and_format():
    """CI must fail when Ruff lint or format checks fail."""
    workflow = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))
    lint_steps = workflow["jobs"]["lint"]["steps"]
    ruff_steps = {
        step["name"]: step for step in lint_steps if step.get("name") in {"Run Ruff linter", "Run Ruff formatter"}
    }

    assert set(ruff_steps) == {"Run Ruff linter", "Run Ruff formatter"}
    for step in ruff_steps.values():
        assert "|| true" not in step["run"]
        assert step.get("continue-on-error") is not True
