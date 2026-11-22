"""Interactive part of `datoso-dev release`: pick packages and versions (planning and execution live elsewhere)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from packaging.version import Version
from rich.console import Console

from datoso_dev.planner import released_version
from datoso_dev.prompts import Option, Prompter
from datoso_dev.state import PackageState, Status
from datoso_dev.versioning import Part, bump, bump_choices
from datoso_dev.workspace import Workspace


def target_version(state: PackageState, part: Part, pre: str | None = None) -> Version:
    """Version a package gets for ``part``: bumps start from its latest release (or ``__version__`` if never)."""
    current = state.package.version
    if part is Part.KEEP:
        return current
    return bump(released_version(state) or current, part, pre=pre)


def _options(
    ws: Workspace, states: Mapping[str, PackageState], *, only: set[str] | None, include_skipped: bool
) -> list[Option]:
    candidates = [name for name, pkg in ws.packages.items() if include_skipped or not pkg.skip]
    width = max((len(name) for name in candidates), default=0)
    options = []
    for name in candidates:
        state = states[name]
        status = state.status
        checked = name in only if only is not None else status is Status.NEEDS_RELEASE
        label = f'{name:<{width}}  {state.package.version!s:<10} {status}'
        options.append(Option(label, name, disabled=state.error, checked=checked and not state.error))
    return options


def choose_release(  # noqa: PLR0913
    ws: Workspace,
    states: Mapping[str, PackageState],
    prompter: Prompter,
    console: Console,
    *,
    only: Iterable[str] | None = None,
    part: Part | None = None,
    pre: str | None = None,
    include_skipped: bool = False,
) -> dict[str, Version] | None:
    """Ask which packages to release and as which version. ``None`` when cancelled (Esc) or nothing was picked."""
    only_set = set(only) if only is not None else None
    options = _options(ws, states, only=only_set, include_skipped=include_skipped)
    names = prompter.checkbox('Packages to release', options)
    if not names:
        console.print('Nothing selected.')
        return None
    selection: dict[str, Version] = {}
    for name in names:
        state = states[name]
        if part is not None:
            selection[name] = target_version(state, part, pre)
            continue
        choices = bump_choices(state.package.version, released=released_version(state), pre=pre)
        version = prompter.select(
            f'{name} {state.package.version}: release as', [Option(c.label, c.version) for c in choices]
        )
        if version is None:
            console.print('Cancelled.')
            return None
        selection[name] = version
    return selection
