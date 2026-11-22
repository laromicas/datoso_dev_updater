"""Release planner (pure): repository states + selected versions → ordered steps of package plans.

Nothing here touches git, GitHub or PyPI; the CLI collects :class:`~datoso_dev.state.PackageState` first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from packaging.version import Version

from datoso_dev.policy import FloorChange, extras_floor_changes, policy_changes
from datoso_dev.state import DEFAULT_BRANCH, PackageState
from datoso_dev.workspace import Workspace, release_steps


class PlanError(ValueError):
    """The selection can't be planned (unknown package, nothing selected)."""


@dataclass(frozen=True)
class PackagePlan:
    """What will happen to one package."""

    name: str
    current: Version  # __version__ in the working tree
    released: Version | None  # latest release (tag or PyPI)
    target: Version
    step: int  # 1-based
    floor_changes: list[FloorChange] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def tag(self) -> str:
        """Git tag / GitHub Release name."""
        return f'v{self.target}'

    @property
    def branch(self) -> str:
        """Release branch the PR is opened from."""
        return f'release/{self.tag}'

    @property
    def title(self) -> str:
        """Commit message and PR title."""
        return f'Release {self.name} {self.tag}'


@dataclass(frozen=True)
class Plan:
    """Steps in dependency order, plus core extras floors to raise after the releases."""

    steps: list[list[PackagePlan]]
    extras: list[FloorChange] = field(default_factory=list)

    @property
    def items(self) -> list[PackagePlan]:
        """Every package plan, in step order."""
        return [item for step in self.steps for item in step]

    def item(self, name: str) -> PackagePlan:
        """Return the plan for one package."""
        return next(item for item in self.items if item.name == name)

    @property
    def ok(self) -> bool:
        """Nothing is blocked."""
        return not any(item.blockers for item in self.items)

    @property
    def runnable_now(self) -> list[PackagePlan]:
        """Unblocked packages of step 1: later steps need earlier ones merged and published first."""
        return [item for item in self.steps[0] if not item.blockers]

    @property
    def waiting(self) -> list[PackagePlan]:
        """Packages of later steps."""
        return [item for step in self.steps[1:] for item in step]


def released_version(state: PackageState) -> Version | None:
    """Latest release of a package: the newest of its last tag and (for PyPI packages) its PyPI version."""
    candidates = [state.last_tag_version]
    if state.package.pypi:
        candidates.append(state.pypi_version)
    return max((v for v in candidates if v is not None), default=None)


def _repo_blockers(state: PackageState, default_branch: str) -> list[str]:
    if state.error:
        return [state.error]
    blockers = []
    if state.changed_files:
        blockers.append(f'{len(state.changed_files)} uncommitted file(s)')
    if state.branch != default_branch:
        blockers.append(f'on branch {state.branch}, not {default_branch}')
    if state.ahead is None or state.behind is None:
        blockers.append('no upstream')
    else:
        if state.behind:
            blockers.append(f'{state.behind} behind origin (pull first)')
        if state.ahead:
            blockers.append(f'{state.ahead} unpushed commit(s)')
    return blockers


def plan_release(
    ws: Workspace,
    states: Mapping[str, PackageState],
    selection: Mapping[str, Version],
    *,
    include_skipped: bool = False,
    default_branch: str = DEFAULT_BRANCH,
) -> Plan:
    """Plan releasing ``selection`` (package name → target version)."""
    if not selection:
        msg = 'nothing selected to release'
        raise PlanError(msg)
    unknown = sorted(set(selection) - set(ws.packages))
    if unknown:
        msg = f'unknown package(s): {", ".join(unknown)}'
        raise PlanError(msg)

    released = {name: released_version(state) for name, state in states.items()}
    floors = policy_changes(ws, selection, released)
    blocked: set[str] = set()
    steps: list[list[PackagePlan]] = []
    for number, names in enumerate(release_steps(ws, selection), start=1):
        step = []
        for name in names:
            pkg, state = ws.packages[name], states[name]
            target = selection[name]
            blockers = _repo_blockers(state, default_branch)
            if pkg.skip and not include_skipped:
                blockers.append('skip = true (use --include-skipped)')
            if released[name] is not None and target <= released[name]:
                blockers.append(f'{target} is not newer than the released {released[name]}')
            blockers += [f'depends on {dep}, which is blocked' for dep in sorted(ws.deps_of(name) & blocked)]
            if blockers:
                blocked.add(name)
            step.append(PackagePlan(name, pkg.version, released[name], target, number, floors[name], blockers))
        steps.append(step)
    return Plan(steps, extras_floor_changes(ws, selection))
