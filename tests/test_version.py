import pytest

from algo_trading.version import version


@pytest.mark.parametrize("environ,expected", [
    ({"APP_VERSION": "1.0.14", "APP_REVISION": "75df87d6999e5a9221a2911ed5e26eb0d870797c"}, "1.0.14 (75df87d)"),
    # A local docker build gets the Dockerfile's defaults.
    ({"APP_VERSION": "dev", "APP_REVISION": "unknown"}, "dev"),
    ({"APP_VERSION": "pr", "APP_REVISION": "abc1234def"}, "pr (abc1234)"),
    ({}, "dev"),
])
def test_version(environ, expected):
    assert version(environ) == expected
