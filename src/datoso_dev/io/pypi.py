"""PyPIPort implementation using the PyPI JSON API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from datoso_dev import __version__

BASE_URL = 'https://pypi.org/pypi'
HTTP_NOT_FOUND = 404


class PyPI:
    """Query the latest published version of a project."""

    def __init__(
        self, base_url: str = BASE_URL, timeout: float = 10, opener: Callable[..., Any] = urllib.request.urlopen
    ) -> None:
        """Configure the API base URL, timeout and (for tests) the URL opener."""
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._open = opener

    def url_for(self, dist_name: str) -> str:
        """JSON API URL of a project."""
        return f'{self.base_url}/{quote(dist_name)}/json'

    def latest_version(self, dist_name: str) -> str | None:
        """Latest version on PyPI, or ``None`` if the project doesn't exist there."""
        request = urllib.request.Request(  # noqa: S310 - fixed https base URL
            self.url_for(dist_name),
            headers={'Accept': 'application/json', 'User-Agent': f'datoso-dev/{__version__}'},
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == HTTP_NOT_FOUND:
                return None
            raise
        return data['info']['version']
