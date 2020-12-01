"""Test meta / packaging"""
import re
import pytest
import subprocess
import aiosmtpd.smtp as a_smtp

# noinspection PyPackageRequirements
from packaging import version


RE_DUNDERVER = re.compile(r"\s*__version__\s?=\s?(['\"])(?P<ver>[^'\"]+)\1\s*$")


@pytest.fixture
def aiosmtpd_version() -> version.Version:
    return version.parse(a_smtp.__version__)


class TestVersion:
    def test_pep440(self, aiosmtpd_version):
        """Ensure version number compliance to PEP-440"""
        assert isinstance(
            aiosmtpd_version, version.Version
        ), "Version number must comply with PEP-440"

    # noinspection PyUnboundLocalVariable
    def test_ge_master(self, aiosmtpd_version):
        """Ensure version is monotonically increasing"""
        reference = "master:aiosmtpd/smtp.py"
        master_smtp = subprocess.check_output(f"git show {reference}").decode()
        for ln in master_smtp.splitlines():
            m = RE_DUNDERVER.match(ln)
            if m:
                break
        else:
            pytest.fail(f"Cannot find __version__ in {reference}!")
        master_ver = version.parse(m.group("ver"))
        assert aiosmtpd_version >= master_ver, "Version number cannot be < master's"
