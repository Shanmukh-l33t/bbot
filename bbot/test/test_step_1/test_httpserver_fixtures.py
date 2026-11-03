"""Regression tests for the httpserver fixtures' teardown contract (#3506).

Every fixture used to call ``server.clear()`` before ``server.check_assertions()``,
which wiped the recorded expectations first, so ``check_assertions()`` could never
fail a test. These tests drive the real fixture functions up to their yield and
through their teardown code (via ``inspect.unwrap``, since the fixture decorator
makes them uncallable directly):

- an expectation failure recorded during the test must surface from teardown
- a handler raising ``AssertionError`` must fail the test (dispatch records it)
- a handler crashing with another exception must fail the test (handler errors)
- a deliberate handler miss must stay silent (brute-force tests miss by design)
"""

import inspect
import urllib.error
import urllib.request
from contextlib import suppress

import pytest

from bbot.test import conftest
from bbot.test.test_step_1 import test_manager_scope_accuracy

# every fixture that owns httpserver teardown (see #3506)
HTTPSERVER_FIXTURES = {
    "bbot_httpserver": conftest.bbot_httpserver,
    "bbot_httpserver_ssl": conftest.bbot_httpserver_ssl,
    "bbot_httpserver_allinterfaces": conftest.bbot_httpserver_allinterfaces,
    "bbot_other_httpservers": test_manager_scope_accuracy.bbot_other_httpservers,
}


def _start_servers(fixture_function):
    """Run a fixture function up to its yield, exactly as pytest would."""
    generator = inspect.unwrap(fixture_function)()
    yielded = next(generator)
    servers = yielded if isinstance(yielded, list) else [yielded]
    return generator, servers


def _fetch(url, **kwargs):
    """Make a request; errors are irrelevant here.

    A miss or a failing handler surfaces as an HTTP error response to the
    client -- the complaint we care about is recorded on the server and must
    arrive at fixture teardown instead.
    """
    with suppress(urllib.error.URLError), urllib.request.urlopen(url, timeout=5, **kwargs) as response:
        response.read()


@pytest.mark.parametrize("fixture_name", sorted(HTTPSERVER_FIXTURES))
def test_recorded_expectation_failure_fails_the_test(fixture_name):
    """A mismatched expectation recorded on the server must fail the test at teardown."""
    generator, servers = _start_servers(HTTPSERVER_FIXTURES[fixture_name])
    # last server in the list, so the others are torn down before the raise
    servers[-1].add_assertion("mismatched expectation")

    with pytest.raises(AssertionError, match="mismatched expectation"):
        next(generator, None)


def test_handler_assertion_error_fails_the_test():
    """A handler raising AssertionError is recorded by dispatch and must fail the test."""
    generator, (server,) = _start_servers(conftest.bbot_httpserver)

    def asserting_handler(request):
        raise AssertionError("handler expectation mismatched")

    server.expect_request("/asserting").respond_with_handler(asserting_handler)
    _fetch(server.url_for("/asserting"))

    with pytest.raises(AssertionError, match="handler expectation mismatched"):
        next(generator, None)


def test_handler_error_fails_the_test():
    """A handler crashing with a non-AssertionError must fail the test via handler errors."""
    generator, (server,) = _start_servers(conftest.bbot_httpserver)

    def broken_handler(request):
        raise ValueError("handler crashed")

    server.expect_request("/broken").respond_with_handler(broken_handler)
    _fetch(server.url_for("/broken"))

    with pytest.raises(ValueError, match="handler crashed"):
        next(generator, None)


def test_deliberate_miss_does_not_fail_the_test():
    """Unmatched requests are the expected case (brute-force tests) and must stay silent."""
    generator, (server,) = _start_servers(conftest.bbot_httpserver)

    _fetch(server.url_for("/never-registered"))

    # must complete without raising
    next(generator, None)
