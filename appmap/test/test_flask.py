"""Test flask integration"""
# pylint: disable=missing-function-docstring

import importlib
import pytest

from appmap._implementation.env import Env

from .web_framework import TestRequestCapture, TestRecording  # pylint: disable=unused-import


@pytest.fixture(name='client')
def flask_client(data_dir, monkeypatch):
    monkeypatch.syspath_prepend(data_dir / 'flask')

    Env.current.set("APPMAP_CONFIG", data_dir / 'flask' / 'appmap.yml')

    import app  # pylint: disable=import-error
    importlib.reload(app)

    with app.app.test_client() as client:  # pylint: disable=no-member
        yield client


@pytest.mark.appmap_enabled
@pytest.mark.parametrize('url,expected', [
    ('/user/test_user', '/user/{username}'),
    ('/post/123', '/post/{post_id}'),
    ('/post/test_user/123/summary', '/post/{username}/{post_id}/summary')
])
def test_path_normalization(client, events, url, expected):
    client.get(url)
    normalized = events[0].http_server_request['normalized_path_info']
    assert normalized == expected
