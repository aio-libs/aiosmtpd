"""Test meta / packaging"""
import aiosmtpd.smtp as a_smtp

from packaging import version


def test_versioning():
    ver = version.parse(a_smtp.__version__)
    assert isinstance(ver, version.Version), "Version number must comply with PEP-0440"
