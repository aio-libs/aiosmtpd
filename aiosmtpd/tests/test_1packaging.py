"""Test meta / packaging"""
import re
import pytest
import subprocess
import aiosmtpd.smtp as a_smtp

from itertools import tee
# noinspection PyPackageRequirements
from packaging import version
from pathlib import Path


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
        cmd = f"git show {reference}".split()
        try:
            master_smtp = subprocess.check_output(cmd).decode()
        except subprocess.CalledProcessError:
            pytest.skip("Skipping due to git error")
            return
        for ln in master_smtp.splitlines():
            m = RE_DUNDERVER.match(ln)
            if m:
                break
        else:
            pytest.fail(f"Cannot find __version__ in {reference}!")
        master_ver = version.parse(m.group("ver"))
        assert aiosmtpd_version >= master_ver, "Version number cannot be < master's"


class TestDocs:
    def test_NEWS_version(self, aiosmtpd_version):
        news_rst = next(Path("..").rglob("*/NEWS.rst"))
        with open(news_rst, "rt") as fin:
            # pairwise() from https://docs.python.org/3/library/itertools.html
            a, b = tee(fin)
            next(b, None)
            for ln1, ln2 in zip(a, b):
                if not ln1[0].isdigit():
                    continue
                ln1 = ln1.strip()
                ln2 = ln2.strip()
                if ln2 != ("=" * len(ln1)):
                    continue
                break
        newsvers = ln1.split()[0]
        newsver = version.parse(newsvers)
        if newsver.base_version < aiosmtpd_version.base_version:
            pytest.fail("NEWS.rst is not updated")
