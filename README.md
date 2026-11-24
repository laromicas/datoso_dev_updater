# datoso_dev_updater → `datoso-dev`

Release and maintenance tool for the [datoso](https://github.com/laromicas/datoso) multi-repo workspace.

> **Status: rewrite complete (phases 1–5).** `status`, `sync`, `ci`, `release` (full release: PR → CI → merge → GitHub
> Release → PyPI, every step, core extras floors, pointers), `notes`, `bump` and `deps`. The legacy scripts and
> `config.ini` are gone: GitHub access goes through the `gh` CLI (`gh auth login`). Design doc in the workspace:
> `docs/tooling/datoso-dev-design.md`.

## Install

```bash
uv tool install -e ./datoso_dev_updater      # from the workspace root; puts `datoso-dev` on PATH
```

## Commands

```bash
datoso-dev status              # dashboard: version, last tag, PyPI, unreleased commits, branch, issues
                               # (a tag ahead of PyPI checks its publish run: running/done is a note, failed an issue)
datoso-dev status --offline    # no git fetch, no PyPI / GitHub queries
datoso-dev status --json       # machine-readable
datoso-dev sync --dry-run      # fetch all repos and show what would change
datoso-dev sync                # switch clean repos to master and pull --rebase (dirty repos are skipped)
datoso-dev ci                  # interactive menu for the managed CI workflow (same as `ci sync` with no names)
datoso-dev ci status           # is each repo's .github/workflows/ci.yml in line with the template? (offline)
datoso-dev ci sync --dry-run   # report what would change for every package
datoso-dev ci sync redump tdc  # only these packages (asks once; --yes to skip)
datoso-dev ci sync --all       # every package (asks once; --yes to skip)
datoso-dev pr --dry-run        # repos on a feature branch: what would be pushed / opened / updated
datoso-dev pr                  # push them and open or update one linked PR per repo (title/description in $EDITOR)
datoso-dev pr redump --title 'Fix X' --body 'Why' --yes   # no editor, no question
datoso-dev prs                 # every open PR of every repo: author, CI, mergeable
datoso-dev prs --merge         # pick PRs to squash-merge (only yours), then switch those repos to master
datoso-dev pointers            # update + push the recorded submodule pointers (never adds new ones; --dry-run, --yes)
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

## Pull requests for a change across repos

`datoso-dev pr` handles every repo whose current branch is not `master` and has commits ahead of `origin/master`:

- **Never commits.** Commit yourself first; a repo with uncommitted (or untracked) files is skipped, so nothing
  unreviewed (cookies, local configs) is pushed. Also skipped: a detached HEAD, a branch behind its upstream, or one
  whose PR was already merged.
- **Commits made on `master`** (local `master` ahead of `origin/master`): it asks for a new branch name (suggesting the
  feature branch the other repos of the run use; `--branch NAME` answers it, empty skips the repo), creates that
  branch at your commits, moves local `master` back to `origin/master`, then pushes and opens the PR like any other.
  Nothing is lost: the commits are on the new branch before `master` moves. A name that already exists (locally or on
  `origin`) is refused; a dirty or diverged `master` is skipped.
- Pushes the branch (never forced) and opens a PR, or just pushes to the PR already open for that branch.
- New PRs get one title and shared description, written in `$VISUAL`/`$EDITOR`: line 1 is the title, then the shared
  text, then a `=== <package>` section per new PR for a note about that repo only. Each PR also lists its own commits.
  `--title`/`--body` skip the editor (required without a terminal); an empty title cancels.
- A new PR is a **draft** when a workspace floor it needs (e.g. `datoso>=1.3.0`) is not on PyPI yet, so its red CI
  means "waiting for core". Running `pr` again once that version is published marks it ready for review.
- Every PR of the run gets a "Related:" line linking the others (`owner/repo#N`), refreshed on each run.

It shows the plan and asks once before pushing. Release PRs are still `datoso-dev release`'s job: merge these first.

## Listing and merging open PRs

`datoso-dev prs [repo…]` lists the open PRs of every repository (core, plugin, seeds, this tool): number, title,
branch, author, CI (passed / failed / pending / none) and whether it can be merged.

`--merge` opens a checkbox of those PRs. **Only PRs opened by the account `gh` is logged in as** can be picked; other
people's (and bots'), drafts and PRs with conflicts are listed but disabled with the reason. PRs with passing (or no) CI
are pre-ticked. After one confirmation each PR is re-read from GitHub (still open, still yours), squash-merged and its
branch deleted; the merged repos are then switched to `master` and pulled (like `sync`), ready for `release`.
Without a terminal pass `--yes`: it merges every green PR of yours, plus the ones with failing CI with
`--include-failing`. Merging doesn't release anything: run `datoso-dev release` afterwards.

## Workspace pointers

`release` commits the pointers of what it releases. For the rest (a merged PR of a skipped package, this tool itself),
`datoso-dev pointers` lists every submodule whose checked-out commit differs from the one `datoso_dev` records: from /
to commit, how many commits and the newest. It **only updates pointers the workspace already records**; it never adds
anything: a submodule that isn't recorded yet (new in `.gitmodules`) is listed as blocked, and untracked files are
ignored, so adding those stays a manual `git submodule add` / `git add`. It updates only pointers whose repo is
**clean, on `master` and pushed**
(otherwise the row says why), so the workspace never points at local-only or unmerged work. It commits just those
submodule paths (untracked files and other changes in `datoso_dev` are left alone) with a message like
`Pointers: a, b` plus each repo's newest commit subjects, and pushes. The workspace itself must be on `master` and not
behind `origin`.

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
2. **Version per package**: patch / minor / major, *keep* when `__version__` is already bumped but unreleased, or
   *custom…* to type one (e.g. `1.3.0` to align a seed with core; Esc or an invalid answer goes back to the choices).
   Never a version below the current one, and always newer than the last release. `--bump` answers this for every package; `--pre a|b|rc` makes pre-releases.
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
.venv/bin/pytest --cov                 # unit + component + offline integration tests; the run fails if coverage drops below 90 %
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
