from setup_helpers import require_python, get_version
from setuptools import setup, find_packages


require_python(0x30600f0)
__version__ = get_version('aiosmtpd/smtp.py')


setup(
    name='aiosmtpd',
    version=__version__,
    description='aiosmtpd - asyncio based SMTP server',
    long_description="""\
This is a server for SMTP and related protocols, similar in utility to the
standard library's smtpd.py module, but rewritten to be based on asyncio for
Python 3.""",
    long_description_content_type="text/x-rst",
    url='http://aiosmtpd.readthedocs.io/',
    keywords='email',
    packages=find_packages(),
    include_package_data=True,
    license='http://www.apache.org/licenses/LICENSE-2.0',
    install_requires=[
        'atpublic',
        ],
    entry_points={
        'console_scripts': ['aiosmtpd = aiosmtpd.main:main'],
        },
    classifiers=[
        'License :: OSI Approved',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'Topic :: Communications :: Email :: Mail Transport Agents',
        'Framework :: AsyncIO',
        ],
    )
