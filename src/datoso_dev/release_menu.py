"""Interactive part of `datoso-dev release`: pick packages and versions (planning and execution live elsewhere)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from packaging.version import Version
from rich.console import Console

from datoso_dev.prompts import Option, Prompter
from datoso_dev.state import PackageState, released_version
from datoso_dev.versioning import Part, bump, bump_choices, check_custom
from datoso_dev.workspace import Workspace

CUSTOM = 'custom'


def custom_version(prompter: Prompter, name: str, state: PackageState, suggested: Version | None) -> Version | None:
    """Ask for a version typed by hand (``None``: back to the choices)."""
    current, released = state.package.version, released_version(state)
    answer = prompter.text(
        f'{name}: version to release', default=str(suggested or ''),
        validate=lambda text: check_custom(text, current, released),
    )  # fmt: skip
    if answer is None or check_custom(answer, current, released):
        return None
    return Version(answer.strip())


def target_version(state: PackageState, part: Part, pre: str | None = None) -> Version:
    """Version a package gets for ``part``: bumps start from its latest release (or ``__version__`` if never)."""
    current = state.package.version
    if part is Part.KEEP:
        return current
    return bump(released_version(state) or current, part, pre=pre)


def _options(
    ws: Workspace, states: Mapping[str, PackageState], *, checked: set[str] | None, include_skipped: bool
) -> list[Option]:
    candidates = [name for name, pkg in ws.packages.items() if pkg.release and (include_skipped or not pkg.skip)]
    width = max((len(name) for name in candidates), default=0)
    options = []
    for name in candidates:
        state = states[name]
        tick = name in checked if checked is not None else state.pending
        label = f'{name:<{width}}  {state.package.version!s:<10} {state.status}'
        options.append(Option(label, name, disabled=state.error, checked=tick and not state.error))
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
    previous: Mapping[str, Version] | None = None,
) -> dict[str, Version] | None:
    """Ask which packages to release and as which version. ``None`` when cancelled or nothing was picked.

    Esc on a version question goes back to the previous one (the first goes back to the packages); Esc on the
    packages cancels. ``previous`` (coming back from a later screen) reopens the last version question with every
    earlier answer pre-selected.
    """
    names = list(previous or ())
    versions = dict(previous or {})
    checked = set(names) if previous else (set(only) if only is not None else None)
    position = len(names) if previous and part is None else 0  # 0: packages; i: version of names[i - 1]
    while True:
        if position == 0:
            picked = prompter.checkbox(
                'Packages to release', _options(ws, states, checked=checked, include_skipped=include_skipped)
            )
            if not picked:
                console.print('Cancelled.' if picked is None else 'Nothing selected.')
                return None
            names, checked = list(picked), set(picked)
            if part is not None:
                return {name: target_version(states[name], part, pre) for name in names}
            position = 1
        elif position > len(names):
            return {name: versions[name] for name in names}
        else:
            name = names[position - 1]
            state = states[name]
            choices = bump_choices(state.package.version, released=released_version(state), pre=pre)
            offered = [Option(c.label, c.version) for c in choices]
            earlier = versions.get(name)
            custom = earlier is not None and all(c.version != earlier for c in choices)
            version = prompter.select(
                f'{name} {state.package.version}: release as',
                [*offered, Option(f'custom ({earlier})' if custom else 'custom…', CUSTOM)],
                default=CUSTOM if custom else earlier,
            )
            if version is None:
                position -= 1
                continue
            if version == CUSTOM:
                version = custom_version(prompter, name, state, earlier)
                if version is None:
                    continue  # back to this package's choices
            versions[name] = version
            position += 1
