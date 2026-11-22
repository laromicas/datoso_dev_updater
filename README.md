# datoso_dev_updater → `datoso-dev`

Release and maintenance tool for the [datoso](https://github.com/laromicas/datoso) multi-repo workspace.

> **Status: phase 1 of the rewrite.** `status` and `sync` are available. The legacy scripts (`update_version.py`,
> `make_prs.py`, `make_release.py`, `*.sh`) still exist until the release flow lands. See the design doc in the
> workspace: `docs/tooling/datoso-dev-design.md`.

## Install

```bash
uv tool install -e ./datoso_dev_updater      # from the workspace root; puts `datoso-dev` on PATH
```

## Commands

```bash
datoso-dev status              # dashboard: version, last tag, PyPI, unreleased commits, branch, issues
datoso-dev status --offline    # no git fetch, no PyPI queries
datoso-dev status --json       # machine-readable
datoso-dev sync --dry-run      # fetch all repos and show what would change
datoso-dev sync                # switch clean repos to master and pull --rebase (dirty repos are skipped)
```

The workspace is auto-detected from the current directory (or the tool's own location). Override it with
`--workspace PATH` or `DATOSO_DEV_WORKSPACE`.

Packages are discovered from the workspace `.gitmodules`: `datoso`, `datoso_plugin_*` and `datoso_seed_*` directories
with a `pyproject.toml`. Per-package settings go in its `pyproject.toml`:

```toml
[tool.datoso-dev]
skip = true    # exclude from releases entirely (e.g. work-in-progress seeds)
pypi = false   # release on GitHub only, never on PyPI (e.g. the datoso_seed_base scaffold)
```

`status` warns if a `pypi = false` package still has a workflow that uploads to PyPI on release, unless the job is
guarded against its own repository (`if: github.repository != 'owner/<package>'`, as in `datoso_seed_base`).

## Development

```bash
cd datoso_dev_updater
uv venv .venv && uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/pytest --cov                 # unit + component + offline integration tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Architecture:
- Side effects live only in `src/datoso_dev/io/` (git, PyPI), behind the `Protocol`s in `ports.py`.
- Tests use the in-memory fakes in `tests/fakes.py`. Tests marked `integration` use real git repositories in temp
  dirs. No test uses the network.
- Core logic (`workspace.py`, `state.py`, `sync.py`) is developed test-first.
