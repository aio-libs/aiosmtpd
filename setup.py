from setup_helpers import require_python, get_version
from setuptools import setup, find_packages


require_python(0x30400f0)
__version__ = get_version('aiosmtpd/version.txt')


setup(
    name            = 'aoismtpd',
    version         = __version__,
    description     = 'aiosmtpd - asyncio based SMTP server',
    long_description= """\
This is a server for SMTP and related protocols, similar in utility to the
standard library's smtpd.py module, but rewritten to be based on asyncio for
Python 3.""",
    author          = 'https://gitlab.com/groups/python-smtpd-hackers',
    url             = 'https://gitlab.com/python-smtpd-hackers/aiosmtpd',
    keywords        = 'email',
    packages= find_packages(),
    include_package_data = True,
    install_requires = [],
    classifiers = [
        'License :: OSI Approved :: Python Software Foundation License',
    ],
    )
