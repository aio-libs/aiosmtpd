"""Test meta / packaging"""
import aiosmtpd.smtp as a_smtp

# noinspection PyPackageRequirements
from packaging import version


def test_versioning():
    """Ensure version number compliance to PEP-440"""
    ver = version.parse(a_smtp.__version__)
    assert isinstance(ver, version.Version), "Version number must comply with PEP-440"
