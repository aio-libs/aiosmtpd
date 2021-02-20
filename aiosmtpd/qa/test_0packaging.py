# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test meta / packaging"""
import re
import subprocess
from itertools import tee
from pathlib import Path

import pytest

# noinspection PyPackageRequirements
from packaging import version

from aiosmtpd import __version__

RE_DUNDERVER = re.compile(r"__version__\s*?=\s*?(['\"])(?P<ver>[^'\"]+)\1\s*$")


@pytest.fixture
def aiosmtpd_version() -> version.Version:
    return version.parse(__version__)


class TestVersion:
    def test_pep440(self, aiosmtpd_version):
        """Ensure version number compliance to PEP-440"""
        assert isinstance(
            aiosmtpd_version, version.Version
        ), "Version number must comply with PEP-440"

    # noinspection PyUnboundLocalVariable
    def test_ge_master(self, aiosmtpd_version, capsys):
        """Ensure version is monotonically increasing"""
        reference = "master:aiosmtpd/__init__.py"
        cmd = f"git show {reference}".split()
        try:
            with capsys.disabled():
                master_smtp = subprocess.check_output(cmd).decode()  # nosec
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
                equals = "=" * len(ln1)
                if not ln2.startswith(equals):
                    continue
                break
        newsvers = ln1.split()[0]
        newsver = version.parse(newsvers)
        if newsver.base_version < aiosmtpd_version.base_version:
            pytest.fail(
                f"NEWS.rst is not updated: "
                f"{newsver.base_version} < {aiosmtpd_version.base_version}"
            )
