"""Version bump rules (pure).

Final releases bump ``major.minor.micro``. Pre-releases use PEP 440 labels ``a`` < ``b`` < ``rc``. A pending
pre-release (``1.2.0rc2``) is finalised by any bump that isn't bigger than the change it already announces.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from packaging.version import Version

PRE_LABELS = ('a', 'b', 'rc')


class VersionError(ValueError):
    """The requested bump is not possible."""


class Part(StrEnum):
    """Which part of the version to bump (``keep`` releases the current, already bumped version)."""

    KEEP = 'keep'
    PATCH = 'patch'
    MINOR = 'minor'
    MAJOR = 'major'


_LEVEL = {Part.PATCH: 0, Part.MINOR: 1, Part.MAJOR: 2}


def _base(version: Version) -> tuple[int, int, int]:
    major, minor, micro = (*version.release, 0, 0)[:3]
    return major, minor, micro


def _level(base: tuple[int, int, int]) -> int:
    """Size of the change a version announces: 2 = major (x.0.0), 1 = minor (x.y.0), 0 = patch."""
    _, minor, micro = base
    if micro:
        return 0
    return 1 if minor else 2


def _bumped(base: tuple[int, int, int], part: Part) -> tuple[int, int, int]:
    major, minor, micro = base
    if part is Part.MAJOR:
        return major + 1, 0, 0
    if part is Part.MINOR:
        return major, minor + 1, 0
    return major, minor, micro + 1


def bump(current: Version, part: Part, *, pre: str | None = None) -> Version:
    """Next version after ``current`` for ``part``; ``pre`` (``a``/``b``/``rc``) makes it a pre-release."""
    if pre is not None and pre not in PRE_LABELS:
        msg = f'unknown pre-release label {pre!r} (use one of {", ".join(PRE_LABELS)})'
        raise VersionError(msg)
    base = _base(current)
    pending = current.pre is not None or current.dev is not None  # current is a pre-release of `base`
    target = base if pending and _LEVEL[part] <= _level(base) else _bumped(base, part)
    text = '.'.join(map(str, target))
    if pre is None:
        return Version(text)
    if target == base and current.pre is not None:
        label, number = current.pre
        if pre == label:
            return Version(f'{text}{pre}{number + 1}')
        if PRE_LABELS.index(pre) < PRE_LABELS.index(label):
            msg = f'{current} is already at {label}; cannot go back to {pre}'
            raise VersionError(msg)
    return Version(f'{text}{pre}1')


@dataclass(frozen=True)
class Choice:
    """One option offered by the release wizard."""

    part: Part
    version: Version

    @property
    def label(self) -> str:
        """Human label, e.g. ``patch (1.1.2)``."""
        if self.part is Part.KEEP:
            return f'keep {self.version} (bumped, not released yet)'
        return f'{self.part} ({self.version})'


def bump_choices(current: Version, *, released: Version | None, pre: str | None = None) -> list[Choice]:
    """Options for a package whose ``__version__`` is ``current`` and whose last release is ``released``."""
    choices: list[Choice] = []
    if released is None or current > released:
        choices.append(Choice(Part.KEEP, current))
    start = released or current
    for part in (Part.PATCH, Part.MINOR, Part.MAJOR):
        version = bump(start, part, pre=pre)
        if all(choice.version != version for choice in choices):
            choices.append(Choice(part, version))
    return choices
