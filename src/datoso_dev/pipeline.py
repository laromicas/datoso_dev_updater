"""Full release of a batch: every step runs PR → CI → merge → GitHub Release → PyPI, then core extras and pointers.

Packages of a step run in parallel. A package starts only once every package of the batch it depends on is on PyPI
(its PR's CI installs the new floor from there); a dependency that didn't finish blocks it, and nothing else. After
the steps, core's optional-dependency floors are raised to the published seeds (direct commit to core's master) and
the workspace submodule pointers are committed. Every task starts from what :func:`~datoso_dev.release.probe_release`
finds on GitHub, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from packaging.version import Version

from datoso_dev.planner import PackagePlan, Plan
from datoso_dev.policy import FloorChange, extras_floor_changes
from datoso_dev.ports import GhPort, GitError, GitPort, PyPIPort
from datoso_dev.pyproject import set_floor
from datoso_dev.release import (
    CHECKS_TIMEOUT,
    PYPI_TIMEOUT,
    PYPROJECT,
    Action,
    Outcome,
    Probe,
    ReleaseResult,
    merge_release_pr,
    open_release_pr,
    probe_release,
    publish_from_branch,
    publish_release,
    update_master,
    wait_for_pypi,
)
from datoso_dev.state import DEFAULT_BRANCH, PackageState
from datoso_dev.workspace import CORE, Workspace, discover

DONE = frozenset({Outcome.PUBLISHED, Outcome.ALREADY_RELEASED})  # on PyPI (or GitHub-only): dependents may start

Notify = Callable[[str, str], None]


@dataclass(frozen=True)
class Waits:
    """How long to wait for CI and PyPI, and the clock to wait with (tests pass fakes)."""

    checks_timeout: float = CHECKS_TIMEOUT
    pypi_timeout: float = PYPI_TIMEOUT
    wait_pypi: bool = True
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class BatchResult:
    """Per-package results in plan order, and the versions that are now released."""

    results: list[ReleaseResult]
    published: dict[str, Version] = field(default_factory=dict)


def full_release(  # noqa: PLR0913, PLR0917
    item: PackagePlan,
    ws: Workspace,
    state: PackageState,
    probe: Probe,
    git: GitPort,
    gh: GhPort,
    pypi: PyPIPort,
    *,
    waits: Waits,
    progress: Callable[[str], None],
    default_branch: str = DEFAULT_BRANCH,
) -> ReleaseResult:
    """Take one package from wherever it is (probe) to released and on PyPI."""
    pkg = ws.packages[item.name]
    action, pr_url = probe.action, probe.pr_url
    if action is Action.ERROR:
        return ReleaseResult(item.name, Outcome.ERROR, probe.detail)
    if action is Action.RELEASED:
        return _already_released(item, ws, pypi, probe, waits)
    if action is Action.OPEN_PR:
        opened = open_release_pr(
            item, pkg, git, gh, since=state.last_tag, default_branch=default_branch, progress=progress
        )
        if opened.outcome is Outcome.MERGED:
            action, pr_url = Action.PUBLISH, opened.pr_url
        elif opened.outcome in {Outcome.OPENED, Outcome.ALREADY_OPEN}:
            action, pr_url = Action.WAIT_MERGE, opened.pr_url
        elif opened.outcome is Outcome.NOTHING_TO_COMMIT:  # version already on master: tag it, no PR
            result = publish_from_branch(
                item, pkg, git, gh, pypi, since=state.last_tag, default_branch=default_branch,
                wait=waits.wait_pypi, progress=progress, timeout=waits.pypi_timeout, sleep=waits.sleep,
                clock=waits.clock,
            )  # fmt: skip
            update_master(git, pkg.path, default_branch)
            return result
        else:
            return opened
    if action is Action.WAIT_MERGE:
        merged = merge_release_pr(
            item, pkg, git, gh, pr_url=pr_url or '', progress=progress, default_branch=default_branch,
            timeout=waits.checks_timeout, sleep=waits.sleep, clock=waits.clock,
        )  # fmt: skip
        if merged.outcome is not Outcome.MERGED:
            return merged
    result = publish_release(
        item, pkg, gh, pypi, pr_url=pr_url or '', since=state.last_tag, wait=waits.wait_pypi, progress=progress,
        timeout=waits.pypi_timeout, sleep=waits.sleep, clock=waits.clock,
    )  # fmt: skip
    update_master(git, pkg.path, default_branch)
    return result


def _already_released(item: PackagePlan, ws: Workspace, pypi: PyPIPort, probe: Probe, waits: Waits) -> ReleaseResult:
    pkg = ws.packages[item.name]
    if pkg.pypi and waits.wait_pypi:
        listed = wait_for_pypi(
            pypi, pkg.dist_name, str(item.target), timeout=waits.pypi_timeout, sleep=waits.sleep, clock=waits.clock
        )
        if not listed:
            detail = f'{item.tag} exists but {item.target} is not on PyPI: check the publish workflow'
            return ReleaseResult(item.name, Outcome.PYPI_PENDING, detail, probe.pr_url)
    return ReleaseResult(item.name, Outcome.ALREADY_RELEASED, item.tag, probe.pr_url)


def release_batch(  # noqa: PLR0913, PLR0917
    plan: Plan,
    ws: Workspace,
    states: Mapping[str, PackageState],
    git: GitPort,
    gh: GhPort,
    pypi: PyPIPort,
    *,
    probes: Mapping[str, Probe],
    waits: Waits | None = None,
    on_progress: Notify | None = None,
    default_branch: str = DEFAULT_BRANCH,
    workers: int = 8,
) -> BatchResult:
    """Release every unblocked package of ``plan``, step by step."""
    waits = waits or Waits()
    notify = on_progress or (lambda _name, _msg: None)
    batch = {item.name for item in plan.items}
    needed = set().union(*(ws.deps_of(name) & batch for name in batch))  # others' CI installs these from PyPI
    results: dict[str, ReleaseResult] = {}
    published: dict[str, Version] = {}

    def run(item: PackagePlan) -> ReleaseResult:
        probe = probes.get(item.name) or probe_release(item, ws.packages[item.name], git, gh)
        item_waits = replace(waits, wait_pypi=True) if item.name in needed else waits
        result = full_release(
            item, ws, states[item.name], probe, git, gh, pypi, waits=item_waits, default_branch=default_branch,
            progress=lambda msg: notify(item.name, msg),
        )  # fmt: skip
        notify(item.name, str(result.outcome))
        return result

    for step in plan.steps:
        ready = []
        for item in step:
            if item.blockers:
                continue
            missing = sorted((ws.deps_of(item.name) & batch) - set(published))
            if missing:
                results[item.name] = ReleaseResult(item.name, Outcome.BLOCKED, f'waits for {", ".join(missing)}')
            else:
                ready.append(item)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for item, result in zip(ready, pool.map(run, ready), strict=True):
                results[item.name] = result
                if result.outcome in DONE:
                    published[item.name] = item.target
    ordered = [results[item.name] for item in plan.items if item.name in results]
    return BatchResult(ordered, published)


def _floors_text(changes: list[FloorChange]) -> str:
    floors = dict.fromkeys(f'{change.dist}>={change.target}' for change in changes)  # one per dist, not per extra
    return ', '.join(floors)


def update_core_extras(
    ws: Workspace, git: GitPort, published: Mapping[str, Version], *, default_branch: str = DEFAULT_BRANCH
) -> ReleaseResult | None:
    """Raise core's extras floors to the published seeds: commit and push to core's master. ``None``: nothing to do."""
    if CORE not in ws.packages or not extras_floor_changes(ws, published):
        return None
    core = ws.packages[CORE].path
    name = f'{CORE} (extras floors)'
    try:
        git.fetch(core)
        unsafe = git.changed_files(core) or git.current_branch(core) != default_branch
        if unsafe:
            todo = _floors_text(extras_floor_changes(ws, published))
            return ReleaseResult(name, Outcome.SKIPPED, f'core is not clean on {default_branch}; raise by hand: {todo}')
        git.pull_rebase(core)
        changes = extras_floor_changes(discover(ws.root), published)  # re-read: master may have moved
        if not changes:
            return None
        path = core / PYPROJECT
        text = path.read_text(encoding='utf-8')
        for change in changes:
            text, _ = set_floor(text, change.dist, change.target, extra=change.extra)
        path.write_text(text, encoding='utf-8')
        message = f'Update extras floors: {_floors_text(changes)}'
        git.commit_paths(core, [PYPROJECT], message)
        git.push(core, default_branch)
    except (GitError, OSError) as exc:
        return ReleaseResult(name, Outcome.ERROR, str(exc))
    return ReleaseResult(name, Outcome.UPDATED, message)


def commit_pointers(
    ws: Workspace,
    git: GitPort,
    published: Mapping[str, Version],
    *,
    extras: bool,
    default_branch: str = DEFAULT_BRANCH,
) -> ReleaseResult | None:
    """Commit and push the workspace's submodule pointers of the released packages (and core, for its extras)."""
    names = set(published) | ({CORE} if extras and CORE in ws.packages else set())
    wanted = {str(ws.packages[name].path.relative_to(ws.root)): name for name in names}
    try:
        changed = [path for path in git.changed_files(ws.root) if path in wanted]
        if not changed:
            return None
        if git.current_branch(ws.root) != default_branch:
            return ReleaseResult('workspace', Outcome.SKIPPED, f'not on {default_branch}; commit the pointers by hand')
        released = ', '.join(f'{name} {version}' for name, version in published.items())
        message = f'Release: {released}' + ('; core extras floors' if extras else '')
        git.commit_paths(ws.root, changed, message)
        git.push(ws.root, default_branch)
    except (GitError, OSError) as exc:
        return ReleaseResult('workspace', Outcome.ERROR, str(exc))
    return ReleaseResult('workspace', Outcome.UPDATED, message)
