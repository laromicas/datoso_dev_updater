# datoso_dev_updater → `datoso-dev`

Release and maintenance tool for the [datoso](https://github.com/laromicas/datoso) multi-repo workspace.

> **Status: phases 1–4 of the rewrite.** `status`, `sync`, `ci`, `release` (full release: PR → CI → merge → GitHub
> Release → PyPI, every step, core extras floors, pointers), `notes`, `bump` and `deps` are available. The legacy scripts (`update_version.py`,
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
updates the branch when a PR is already open). An open PR whose branch already has the current template shows as
**PR open (waiting for merge)** and is left alone. Packages can be named in short form: `redump`, `internetarchive`. It only touches repos that are clean, on `master` and in sync with
`origin`. Targets: the plugin and every seed (core keeps its own `pytest.yml`; skipped packages need
`--include-skipped`). The template is `src/datoso_dev/templates/ci.yml`: install from PyPI floors, `ruff check` (pyflakes rules only),
import every module, `datoso doctor <seed>`, and `pytest` when a `tests/` directory exists.

## Releasing

```bash
datoso-dev release                                   # interactive wizard
datoso-dev release --only redump,tdc --bump patch --dry-run   # just the plan
datoso-dev release --only redump --bump patch --yes --merge   # full release, non-interactive
datoso-dev release --only redump --bump patch --yes  # stop at the PRs (you merge)
datoso-dev release ... --no-wait                     # don't wait for PyPI (except for batch dependencies)
datoso-dev notes redump [1.0.2]                      # edit a GitHub Release's notes in $EDITOR
datoso-dev bump redump minor [--pre rc]              # version only, working tree, no git
datoso-dev deps [--fix]                              # floors behind the latest released core / IA plugin
```

The wizard shows the status table, then asks:

1. **Packages to release**: a checkbox, with packages that need a release pre-ticked (`--only` pre-ticks those
   instead). Skipped packages and templates are hidden (`--include-skipped` shows skipped ones).
2. **Version per package**: patch / minor / major, or *keep* when `__version__` is already bumped but unreleased.
   Never a version below the current one. `--bump` answers this for every package; `--pre a|b|rc` makes pre-releases.
3. **The plan**: steps in dependency order, version changes, dependency floors, blockers (dirty, wrong branch,
   behind, version not newer than the last release, blocked dependency), core's extras floors, and for step 1 what
   GitHub already shows: *open release PR*, *PR open: merge it first*, *tag + publish release*, *already released*.
4. **What should this run do?**
   - **Full release** (`--yes --merge`): for each step, in parallel per package: open the PR → wait for CI (up to 30
     min) → squash-merge it (deleting the branch) and fast-forward local `master` → tag the merge commit and publish
     the GitHub Release with generated notes (that triggers the PyPI upload) → wait until PyPI lists the version (up
     to 10 min). A step starts once its dependencies in the batch are on PyPI. Then core's extras floors are raised to
     the published seeds (direct commit + push to core's `master`, only if core is clean on `master`) and the
     workspace submodule pointers of the released packages are committed and pushed (`Release: … ; core extras
     floors`).
   - **Stop at the PRs** (`--yes`): open the step-1 PRs, and publish the ones already merged. You merge on GitHub and
     run `release` again.

Opening a PR: branch `release/vX.Y.Z` from `origin/master`, set `__version__`, apply the floors, commit
`Release <pkg> vX.Y.Z`, push, open the PR (body: the version change, the floors and the commits since the last tag).
Publishing: `gh release create vX.Y.Z --target <merge commit> --generate-notes --notes-start-tag <last tag>`
(`--prerelease` for a/b/rc); the PyPI wait is skipped for `pypi = false`.

Esc goes back one question (keeping your earlier answers selected): from a version to the previous one, from
the first version to the packages, from *What should this run do?* to the versions; Esc on the packages exits.
Dependency policy: every released package follows the latest core and IA plugin
(`datoso>=…` is raised to the new core when core is in the batch, else to the latest released core); floors are never
lowered; packages outside the batch are untouched.

**When something fails** (CI red, merge refused, PyPI slow), that package stops with the PR left open, its dependents
in the batch are *not started*, and everything else continues. Fix it and run `release` again with the same packages
and *keep* versions: every task starts from what GitHub and PyPI show, so it resumes where it stopped. A merged PR is
never re-opened, an existing release never re-created, and failed edits are rolled back so `master` stays clean.

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
- Core logic (`workspace.py`, `state.py`, `sync.py`, `ci.py`, `ci_menu.py`, `versioning.py`, `pyproject.py`,
  `policy.py`, `planner.py`, `release.py`, `release_menu.py`) is developed test-first. `tests/io/test_gh.py` checks the
  exact `gh` arguments without calling GitHub.
