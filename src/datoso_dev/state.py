"""Collect and assess the state of every package repository (git + PyPI)."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from packaging.version import InvalidVersion, Version

from datoso_dev.ports import GitError, GitPort, PyPIPort
from datoso_dev.workspace import Package, Workspace

DEFAULT_BRANCH = 'master'


class Status(StrEnum):
    """Release status of a package."""

    NEEDS_RELEASE = 'needs release'  # unreleased commits; the version still has to be bumped
    READY = 'ready to release'  # version already ahead of the last release: only tag + publish missing
    FIRST_RELEASE = 'first release'  # never tagged nor published (its first PyPI upload creates the project)
    TEMPLATE = 'template'  # release = false: copied by init.sh, never released
    UP_TO_DATE = 'up to date'
    SKIPPED = 'skipped'
    ERROR = 'error'


@dataclass(frozen=True)
class PackageState:
    """Snapshot of one package's repository and publication state."""

    package: Package
    branch: str | None = None
    changed_files: tuple[str, ...] = ()
    ahead: int | None = None
    behind: int | None = None
    last_tag: str | None = None
    last_tag_version: Version | None = None
    commits_since_tag: int = 0
    pypi_version: Version | None = None
    pypi_error: str | None = None
    error: str | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        """Overall release status."""
        if self.error:
            return Status.ERROR
        if self.package.skip:
            return Status.SKIPPED
        if not self.package.release:
            return Status.TEMPLATE
        return self._release_status()

    def _release_status(self) -> Status:
        released = released_version(self)
        if released is None:
            return Status.FIRST_RELEASE
        if self.package.version > released:
            return Status.READY
        return Status.NEEDS_RELEASE if self.commits_since_tag > 0 else Status.UP_TO_DATE

    @property
    def pending(self) -> bool:
        """Whether the package has something to release."""
        return self.status in {Status.NEEDS_RELEASE, Status.READY, Status.FIRST_RELEASE}


def released_version(state: PackageState) -> Version | None:
    """Latest release of a package: the newest of its last tag and (for PyPI packages) its PyPI version."""
    candidates = [state.last_tag_version]
    if state.package.pypi:
        candidates.append(state.pypi_version)
    return max((v for v in candidates if v is not None), default=None)


def latest_release_tag(tags: Iterable[str]) -> tuple[str, Version] | None:
    """Return the highest version tag (``vX.Y.Z`` or ``X.Y.Z``), ignoring non-version tags."""
    best: tuple[str, Version] | None = None
    for tag in tags:
        try:
            version = Version(tag.removeprefix('v'))
        except InvalidVersion:
            continue
        if best is None or version > best[1]:
            best = (tag, version)
    return best


def assess(state: PackageState, *, default_branch: str = DEFAULT_BRANCH, query_pypi: bool = True) -> list[str]:
    """Human-readable warnings about a package state."""
    issues: list[str] = []
    if state.changed_files:
        issues.append(f'{len(state.changed_files)} uncommitted file(s)')
    if state.branch and state.branch != default_branch:
        issues.append(f'on branch {state.branch}')
    if state.ahead is None:
        issues.append('no upstream')
    else:
        if state.behind:
            issues.append(f'{state.behind} behind origin')
        if state.ahead:
            issues.append(f'{state.ahead} unpushed commit(s)')
    if state.last_tag_version and state.package.version < state.last_tag_version:
        issues.append(f'version {state.package.version} is older than tag {state.last_tag}')
    if query_pypi and _published(state.package):
        issues.extend(_pypi_issues(state))
    if not state.package.pypi and state.package.publishes_to_pypi:
        issues.append('pypi = false but a workflow publishes to PyPI on release')
    return issues


def _published(package: Package) -> bool:
    """Whether the package is expected on PyPI (and worth querying)."""
    return package.pypi and not package.skip


def _pypi_issues(state: PackageState) -> list[str]:
    if state.pypi_error:
        return [f'PyPI unavailable: {state.pypi_error}']
    if state.pypi_version is None:
        return [f'{state.last_tag} tagged but not on PyPI'] if state.last_tag else []  # else: first release
    if state.last_tag_version and state.pypi_version < state.last_tag_version:
        return [f'{state.last_tag} tagged but PyPI has {state.pypi_version} (publish failed?)']
    return []


def collect_state(
    package: Package, git: GitPort, pypi: PyPIPort, *, fetch: bool = False, query_pypi: bool = True
) -> PackageState:
    """Collect the state of one package. Git failures are captured in ``error``, never raised."""
    repo = package.path
    try:
        if fetch:
            git.fetch(repo)
        branch = git.current_branch(repo)
        changed = tuple(git.changed_files(repo))
        ahead_behind = git.ahead_behind(repo)
        tag = latest_release_tag(git.tags(repo))
        commits = git.count_commits(repo, tag[0] if tag else None)
    except (GitError, OSError) as exc:
        return PackageState(package=package, error=str(exc))

    pypi_version: Version | None = None
    pypi_error: str | None = None
    if query_pypi and _published(package):
        try:
            raw = pypi.latest_version(package.dist_name)
            pypi_version = Version(raw) if raw else None
        except (OSError, ValueError) as exc:
            pypi_error = str(exc)

    state = PackageState(
        package=package,
        branch=branch,
        changed_files=changed,
        ahead=ahead_behind[0] if ahead_behind else None,
        behind=ahead_behind[1] if ahead_behind else None,
        last_tag=tag[0] if tag else None,
        last_tag_version=tag[1] if tag else None,
        commits_since_tag=commits,
        pypi_version=pypi_version,
        pypi_error=pypi_error,
    )
    state.issues.extend(assess(state, query_pypi=query_pypi))
    return state


def collect_states(  # noqa: PLR0913
    workspace: Workspace,
    git: GitPort,
    pypi: PyPIPort,
    *,
    fetch: bool = False,
    query_pypi: bool = True,
    workers: int = 8,
) -> list[PackageState]:
    """Collect states for all packages in parallel, preserving workspace order."""
    packages = list(workspace.packages.values())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda pkg: collect_state(pkg, git, pypi, fetch=fetch, query_pypi=query_pypi), packages))
