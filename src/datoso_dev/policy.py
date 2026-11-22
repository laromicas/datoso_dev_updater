"""Dependency policy (pure): every package being released follows the latest core and IA plugin.

For each released package ``P`` and each *followed* package ``F`` it depends on, ``P``'s ``>=`` floor on ``F`` is
raised to the version of ``F`` that will be the latest: the new one if ``F`` is in the same batch, else ``F``'s latest
release. Floors are never lowered, and packages outside the batch are never modified.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from datoso_dev.workspace import CORE, Workspace

FOLLOWED = (CORE, 'datoso_plugin_internetarchive')


@dataclass(frozen=True)
class FloorChange:
    """``package``'s requirement on ``dependency`` goes from ``>=current`` to ``>=target``."""

    package: str
    dependency: str  # workspace package name
    dist: str  # requirement name as written in pyproject.toml
    current: Version | None
    target: Version
    extra: str | None = None  # set for an optional-dependency (core extras), None for runtime dependencies


def current_floor(req: Requirement) -> Version | None:
    """Highest ``>=`` bound of a requirement, or ``None`` when it has none."""
    floors = [Version(spec.version) for spec in req.specifier if spec.operator == '>=']
    return max(floors, default=None)


def _changes_for(ws: Workspace, name: str, targets: Mapping[str, Version | None]) -> list[FloorChange]:
    index = ws.by_dist_name()
    changes = []
    for req in ws.packages[name].requirements:
        dependency = index.get(canonicalize_name(req.name))
        if dependency not in FOLLOWED or dependency == name:
            continue
        target = targets.get(dependency)
        floor = current_floor(req)
        if target is not None and (floor is None or floor < target):
            changes.append(FloorChange(name, dependency, req.name, floor, target))
    return changes


def policy_changes(
    ws: Workspace, batch: Mapping[str, Version], released: Mapping[str, Version | None]
) -> dict[str, list[FloorChange]]:
    """Floor changes for every package in ``batch`` (name → new version), given each package's latest release."""
    targets = {name: batch.get(name, released.get(name)) for name in FOLLOWED}
    return {name: _changes_for(ws, name, targets) for name in batch}


def violations(
    ws: Workspace, released: Mapping[str, Version | None], *, include_skipped: bool = False
) -> dict[str, list[FloorChange]]:
    """Return packages whose floors lag the latest *released* core/plugin (``datoso-dev deps``)."""
    targets = {name: released.get(name) for name in FOLLOWED}
    found = {}
    for name, pkg in ws.packages.items():
        if pkg.skip and not include_skipped:
            continue
        changes = _changes_for(ws, name, targets)
        if changes:
            found[name] = changes
    return found


def extras_floor_changes(ws: Workspace, batch: Mapping[str, Version]) -> list[FloorChange]:
    """Core's ``[project.optional-dependencies]`` floors to raise for the seeds/plugin released in ``batch``.

    Applied after those releases (direct commit to core's master), so a floor never points at a version that isn't
    published yet.
    """
    if CORE not in ws.packages:
        return []
    index = ws.by_dist_name()
    changes = []
    for extra, reqs in ws.packages[CORE].extras:
        for req in reqs:
            dependency = index.get(canonicalize_name(req.name))
            if dependency is None or dependency == CORE or dependency not in batch:
                continue
            floor = current_floor(req)
            if floor is None or floor < batch[dependency]:
                changes.append(FloorChange(CORE, dependency, req.name, floor, batch[dependency], extra))
    return changes
