# datoso_dev_updater → `datoso-dev`

Release and maintenance tool for the [datoso](https://github.com/laromicas/datoso) multi-repo workspace.

> **Status: phases 1–3 of the rewrite.** `status`, `sync`, `ci`, `release` (PR, then tag + GitHub Release + PyPI wait), `bump` and `deps`
> are available. The legacy scripts (`update_version.py`,
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
datoso-dev release --only redump --bump patch --yes  # non-interactive
datoso-dev release ... --no-wait                     # don't wait for PyPI after publishing
datoso-dev bump redump minor [--pre rc]              # version only, working tree, no git
datoso-dev deps [--fix]                              # floors behind the latest released core / IA plugin
```

**A release takes two runs, with your merge in between:**

1. `datoso-dev release` → opens the release PR.
2. You merge it on GitHub once CI passes.
3. `datoso-dev release` again (pick the same package; *keep* the version) → tags the PR's merge commit, publishes the
   GitHub Release with generated notes (which triggers the PyPI upload workflow) and waits until PyPI lists the version.
4. `datoso-dev sync`, then commit the submodule pointers in the workspace.

Before asking anything, each run checks GitHub for every package: no release PR → *open release PR*; an open one →
*PR open: merge it first*; a merged one → *tag + publish release*; a GitHub Release for the tag → *already released*.
The plan table shows that action, and the confirmation names it ("Open 1 release PR(s) and publish 1 release(s)?").

The wizard shows the status table, then asks:

1. **Packages to release**: a checkbox, with packages that need a release pre-ticked (`--only` pre-ticks those
   instead). Skipped packages are hidden unless `--include-skipped`.
2. **Version per package**: patch / minor / major, or *keep* when `__version__` is already bumped but unreleased.
   `--bump` answers this for every package; `--pre a|b|rc` makes pre-releases.
3. **The plan**: steps in dependency order, version changes, dependency floors, blockers (dirty, wrong branch,
   behind, version not newer than the last release, blocked dependency), and core's extras floors for later.
4. **Confirm** the actions. Opening a PR, for each step-1 package in parallel: branch `release/vX.Y.Z` from
   `origin/master`, set `__version__`, apply the floors, commit `Release <pkg> vX.Y.Z`, push, open the PR (body: the
   version change, the floors and the commits since the last tag). Publishing: `gh release create vX.Y.Z --target
   <merge commit> --generate-notes --notes-start-tag <last tag>` (`--prerelease` for a/b/rc), then poll PyPI for up to
   10 minutes (skipped for `pypi = false`).

Esc cancels at any point. Dependency policy: every released package follows the latest core and IA plugin
(`datoso>=…` is raised to the new core when core is in the batch, else to the latest released core); floors are never
lowered; packages outside the batch are untouched.

**One step at a time.** A package that depends on another package in the batch (e.g. a seed when core is released
too) waits until that release is on PyPI, because its PR CI installs the new floor from PyPI. Release step 1, then run
`release` again: the rest becomes step 1. Still manual (phase 4): merging, core's extras floors, workspace pointers.

Re-running is always safe: a merged PR is never re-opened, an existing release is never re-created, a pushed release
branch resumes at "open PR", and failed edits are rolled back so `master` stays clean.

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
