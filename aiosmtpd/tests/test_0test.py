"""Test the sanity of the test suite itself"""

import re
import pytest

from aiosmtpd.testing import statuscodes
from itertools import combinations


ENFORCE_ENHANCED_STATUS_CODES = False
"""Whether to do strict compliance checking against RFC 2034 § 4"""

RE_ESC = re.compile(rb"\d+\.\d+\.\d+")


# noinspection PyUnresolvedReferences
@pytest.fixture(scope="module", autouse=True)
def exit_on_fail(request):
    # Behavior of this will be undefined if tests are running in parallel.
    # But since parallel running is not practically possible (the ports will conflict),
    # then I don't think that will be a problem.
    failcount = request.session.testsfailed
    yield
    if request.session.testsfailed != failcount:
        pytest.exit("Test Suite is Not Sane!")


STATUS_CODES = {
    k: v for k, v in vars(statuscodes.SMTP_STATUS_CODES).items() if k.startswith("S")
}


def test_statuscodes_elemtype():
    """Ensure status codes are instances of StatusCode"""
    for key, value in STATUS_CODES.items():
        assert isinstance(value, statuscodes.StatusCode)


def test_statuscode_nameval():
    """Ensure each status code constant has SMTP Code embedded in the name"""
    for key, value in STATUS_CODES.items():
        assert int(key[1:4]) == value.code


def test_statuscode_enhanced():
    """Compliance with RFC 2034 § 4"""
    for key, value in STATUS_CODES.items():
        assert isinstance(value, statuscodes.StatusCode)
        m = RE_ESC.match(value.mesg)
        if ENFORCE_ENHANCED_STATUS_CODES:
            assert m is not None, f"{key} does not have Enhanced Status Code"
        elif m is None:
            continue
        esc1, dot, rest = m.group().partition(b".")
        # noinspection PyTypeChecker
        assert str(value.code // 100) == esc1.decode(), (
            f"{key}: First digit of Enhanced Status Code different from first digit "
            f"of Standard Status Code"
        )


def test_commands():
    """
    Ensure lists in statuscodes are individual objects, so changes in one list won't
    affect the other lists
    """
    lists = [
        statuscodes._COMMON_COMMANDS,
        statuscodes.SUPPORTED_COMMANDS_NOTLS,
        statuscodes.SUPPORTED_COMMANDS_TLS,
        statuscodes.SUPPORTED_COMMANDS_LMTP,
    ]
    for one, two in combinations(lists, 2):
        assert one is not two
