"""Contract tests for the PyPI adapter (no network)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from datoso_dev.io.pypi import PyPI


class RecordingOpener:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload, self.error, self.requests = payload, error, []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.payload).encode())


def test_builds_expected_request():
    opener = RecordingOpener({'info': {'version': '1.0.1'}})
    assert PyPI(opener=opener, timeout=5).latest_version('datoso-seed-redump') == '1.0.1'
    request, timeout = opener.requests[0]
    assert request.full_url == 'https://pypi.org/pypi/datoso-seed-redump/json'
    assert request.get_header('Accept') == 'application/json'
    assert request.get_header('User-agent').startswith('datoso-dev/')
    assert timeout == 5


def test_not_found_returns_none():
    err = urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
    assert PyPI(opener=RecordingOpener(error=err)).latest_version('nope') is None


def test_other_http_errors_propagate_as_oserror():
    err = urllib.error.HTTPError('u', 503, 'Unavailable', {}, None)
    with pytest.raises(OSError, match='Unavailable'):
        PyPI(opener=RecordingOpener(error=err)).latest_version('datoso')


def test_custom_base_url():
    assert PyPI(base_url='http://localhost:8080/pypi/').url_for('x y') == 'http://localhost:8080/pypi/x%20y/json'


def test_has_version():
    opener = RecordingOpener({'info': {'version': '1.1.0'}})
    assert PyPI(opener=opener).has_version('datoso-seed-tdc', '1.1.0') is True
    assert opener.requests[0][0].full_url == 'https://pypi.org/pypi/datoso-seed-tdc/1.1.0/json'
    err = urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
    assert PyPI(opener=RecordingOpener(error=err)).has_version('datoso-seed-tdc', '1.1.0') is False
