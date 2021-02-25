######################################
 aiosmtpd - asyncio based SMTP server
######################################

| |github license| |_| |PyPI Version| |PyPI Python|
| |GA badge| |codecov| |_| |LGTM.com| |readthedocs| |_|
| |PullRequests| |_| |LastCommit|
|

.. |_| unicode:: 0xA0
   :trim:
.. |github license| image:: https://img.shields.io/github/license/aio-libs/aiosmtpd
   :target: https://github.com/aio-libs/aiosmtpd/blob/master/LICENSE
   :alt: Project License on GitHub
.. .. For |GA badge|, don't forget to check actual workflow name in unit-testing-and-coverage.yml
.. |GA badge| image:: https://github.com/aio-libs/aiosmtpd/workflows/aiosmtpd%20CI/badge.svg
   :target: https://github.com/aio-libs/aiosmtpd/actions
   :alt: GitHub Actions status
.. |codecov| image:: https://codecov.io/github/aio-libs/aiosmtpd/coverage.svg?branch=master
   :target: https://codecov.io/github/aio-libs/aiosmtpd?branch=master
   :alt: Code Coverage
.. |LGTM.com| image:: https://img.shields.io/lgtm/grade/python/github/aio-libs/aiosmtpd.svg?logo=lgtm&logoWidth=18
   :target: https://lgtm.com/projects/g/aio-libs/aiosmtpd/context:python
   :alt: Semmle/LGTM.com quality
.. |readthedocs| image:: https://img.shields.io/readthedocs/aiosmtpd?logo=Read+the+Docs
   :target: https://aiosmtpd.readthedocs.io/en/latest/?badge=latest
   :alt: Documentation Status
.. |PyPI Version| image:: https://badge.fury.io/py/aiosmtpd.svg
   :target: https://badge.fury.io/py/aiosmtpd
   :alt: PyPI Package
.. |PyPI Python| image:: https://img.shields.io/pypi/pyversions/aiosmtpd.svg
   :target: https://pypi.org/project/aiosmtpd/
   :alt: Supported Python Versions
.. .. Do NOT include the Discourse badge!
.. .. Below are badges just for PyPI
.. |PullRequests| image:: https://img.shields.io/github/issues-pr/aio-libs/aiosmtpd?logo=GitHub
   :target: https://github.com/aio-libs/aiosmtpd/pulls
   :alt: GitHub pull requests
.. |LastCommit| image:: https://img.shields.io/github/last-commit/aio-libs/aiosmtpd?logo=GitHub
   :target: https://github.com/aio-libs/aiosmtpd/commits/master
   :alt: GitHub last commit

This is a server for SMTP and related MTA protocols,
similar in utility to the standard library's |smtpd.py|_ module,
but rewritten to be based on ``asyncio`` for Python 3.6+.

Please visit the `Project Homepage`_ for more information.

.. _`Project Homepage`: https://aiosmtpd.readthedocs.io/
.. |smtpd.py| replace:: ``smtpd.py``
.. _`smtpd.py`: https://docs.python.org/3/library/smtpd.html


Signing Keys
============

Starting version 1.3.1,
files provided through PyPI or `GitHub Releases`_
will be signed using one of the following GPG Keys:

+-------------------------+----------------+------------------------------+
| GPG Key ID              | Owner          | Email                        |
+=========================+================+==============================+
| ``5D60 CE28 9CD7 C258`` | Pandu E POLUAN | pepoluan at gmail period com |
+-------------------------+----------------+------------------------------+

.. _`GitHub Releases`: https://github.com/aio-libs/aiosmtpd/releases
