# datoso_dev_updater → `datoso-dev`

Release and maintenance tool for the [datoso](https://github.com/laromicas/datoso) multi-repo workspace.

> **Status: phases 1–2 of the rewrite.** `status`, `sync` and `ci` are available. The legacy scripts (`update_version.py`,
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
datoso-dev ci                  # interactive menu for the managed CI workflow (same as `ci sync` with no names)
datoso-dev ci status           # is each repo's .github/workflows/ci.yml in line with the template? (offline)
datoso-dev ci sync --dry-run   # report what would change for every package
datoso-dev ci sync redump tdc  # only these packages (asks once; --yes to skip)
datoso-dev ci sync --all       # every package (asks once; --yes to skip)
```

`datoso-dev ci` is **interactive by default**. It checks every repo, shows a status table (will open PR / will update
PR / up to date / skipped with the reason), then a menu:

- **Open / update PRs for selected packages…**: a checkbox list (space toggles, `a` all, `i` invert, Esc goes back). Packages that
  need nothing, or can't be touched safely, are listed but disabled with the reason. You confirm before anything is
  pushed, and the status is re-checked afterwards.
- **Show workflow diff for a package…**: unified diff between the repo's workflow and the template.
- **Pull repos that are behind or not on master…**: after you merge PRs on GitHub, your local clones are behind and
  get skipped. This pulls them (same rules as `datoso-dev sync`: clean repos only) and re-checks.
- **Refresh status** / **Quit**.

**Esc** (or Ctrl-C) cancels any prompt: back to the menu from a list or a confirmation, and quit from the menu itself.
The status is re-checked every time you choose *Open / update PRs…*, so a menu left open while you merge PRs never
offers a stale selection. When nothing needs a PR, it says so and goes back to the menu.

Doing everything at once is opt-in: `ci sync --all`. Without a terminal (scripts, CI), pass names or `--all`, plus
`--yes`.

For each selected package it commits the workflow on branch `datoso-dev/ci`, force-pushes it, and opens a PR (or just
updates the branch when a PR is already open). It only touches repos that are clean, on `master` and in sync with
`origin`. Targets: the plugin and every seed (core keeps its own `pytest.yml`; skipped packages need
`--include-skipped`). The template is `src/datoso_dev/templates/ci.yml`: install from PyPI floors, `ruff check` (pyflakes rules only),
import every module, `datoso doctor <seed>`, and `pytest` when a `tests/` directory exists.

## Development

```bash
cd datoso_dev_updater
uv venv .venv && uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/pytest --cov                 # unit + component + offline integration tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Architecture:
- Side effects live only in `src/datoso_dev/io/` (git, gh, PyPI), behind the `Protocol`s in `ports.py`. Prompts go
  through `prompts.Prompter` (questionary in a terminal, scripted answers in tests).
- Tests use the in-memory fakes in `tests/fakes.py`. Tests marked `integration` use real git repositories in temp
  dirs. No test uses the network.
- Core logic (`workspace.py`, `state.py`, `sync.py`, `ci.py`, `ci_menu.py`) is developed test-first. `tests/io/test_gh.py` checks the
  exact `gh` arguments without calling GitHub.
