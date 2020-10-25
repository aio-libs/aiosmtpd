=========================================
 aiosmtpd - An asyncio based SMTP server
=========================================

| |github license| |travis ci| |codecov| |LGTM.com| |readthedocs| |PyPI|
|
| |Discourse|

.. |github license| image:: https://img.shields.io/github/license/aio-libs/aiosmtpd
   :target: https://github.com/aio-libs/aiosmtpd/blob/master/LICENSE
   :alt: Project License on GitHub
.. |travis ci| image:: https://travis-ci.com/aio-libs/aiosmtpd.svg?branch=master
   :target: https://travis-ci.com/github/aio-libs/aiosmtpd
   :alt: Travis CI Build Status
.. |codecov| image:: https://codecov.io/github/aio-libs/aiosmtpd/coverage.svg?branch=master
   :target: https://codecov.io/github/aio-libs/aiosmtpd?branch=master
   :alt: Code Coverage
.. |LGTM.com| image:: https://img.shields.io/lgtm/grade/python/github/aio-libs/aiosmtpd.svg?logo=lgtm&logoWidth=18
   :target: https://lgtm.com/projects/g/aio-libs/aiosmtpd/context:python
   :alt: Semmle/LGTM.com quality
.. |readthedocs| image:: https://readthedocs.org/projects/aiosmtpd/badge/?version=latest
   :target: https://aiosmtpd.readthedocs.io/en/latest/?badge=latest
   :alt: Documentation Status
.. |PyPI| image:: https://badge.fury.io/py/aiosmtpd.svg
   :target: https://badge.fury.io/py/aiosmtpd
   :alt: PyPI Package
.. |Discourse| image:: https://img.shields.io/discourse/status?server=https%3A%2F%2Faio-libs.discourse.group%2F&style=social
   :target: https://aio-libs.discourse.group/
   :alt: Discourse status

The Python standard library includes a basic
`SMTP <http://www.faqs.org/rfcs/rfc5321.html>`__ server in the
`smtpd <https://docs.python.org/3/library/smtpd.html>`__ module, based on the
old asynchronous libraries
`asyncore <https://docs.python.org/3/library/asyncore.html>`__ and
`asynchat <https://docs.python.org/3/library/asynchat.html>`__.  These modules
are quite old and are definitely showing their age.  asyncore and asynchat are
difficult APIs to work with, understand, extend, and fix.

With the introduction of the
`asyncio <https://docs.python.org/3/library/asyncio.html>`__ module in Python
3.4, a much better way of doing asynchronous I/O is now available.  It seems
obvious that an asyncio-based version of the SMTP and related protocols are
needed for Python 3.  This project brings together several highly experienced
Python developers collaborating on this reimplementation.

This package provides such an implementation of both the SMTP and LMTP
protocols.


Requirements
============

You need **at least Python 3.6** to use this library.  Both Windows and \*nix are
supported.


License
=======

``aiosmtpd`` is released under the Apache License version 2.0.


Project details
===============

As of 2016-07-14, aiosmtpd has been put under the `aio-libs
<https://github.com/aio-libs>`__ umbrella project and moved to GitHub.

* Project home: https://github.com/aio-libs/aiosmtpd
* Report bugs at: https://github.com/aio-libs/aiosmtpd/issues
* Git clone: https://github.com/aio-libs/aiosmtpd.git
* Documentation: http://aiosmtpd.readthedocs.io/
* StackOverflow: https://stackoverflow.com/questions/tagged/aiosmtpd

The best way to contact the developers is through the GitHub links above.
You can also request help by submitting a question on StackOverflow.


Building
========

You can install this package in a virtual environment like so::

    $ python3 -m venv /path/to/venv
    $ source /path/to/venv/bin/activate
    $ python setup.py install

This will give you a command line script called ``smtpd`` which implements the
SMTP server.  Use ``smtpd --help`` for details.

You will also have access to the ``aiosmtpd`` library, which you can use as a
testing environment for your SMTP clients.  See the documentation links above
for details.


Developing
==========

You'll need the `tox <https://pypi.python.org/pypi/tox>`__ tool to run the
test suite for Python 3.  Once you've got that, run::

    $ tox

Individual tests can be run like this::

    $ tox -e py36-nocov -- -P <pattern>

where *<pattern>* is a Python regular expression matching a test name.

You can also add the ``-E`` option to boost debugging output, e.g.::

    $ tox -e py36-nocov -- -E

and these options can be combined::

    $ tox -e py36-nocov -- -P test_connection_reset_during_DATA -E


Supported 'testenvs'
------------------------

In general, the ``-e`` parameter to tox specifies one (or more) **testenv**
to run (separate using comma if more than one testenv). The following testenvs
have been configured and tested:

* ``{py36,py37,py38,py39,pypy3}-{nocov,cov,diffcov,profile}``

  Specifies the interpreter to run and the kind of testing to perform.

  - ``nocov`` = no coverage testing. Tests will run verbosely.
  - ``cov`` = with coverage testing. Tests will run in brief mode
    (showing a single character per test run)
  - ``diffcov`` = with diff-coverage report (showing difference in
    coverage compared to previous commit). Tests will run in brief mode
  - ``profile`` = no coverage testing, but code profiling instead.
    This must be **invoked manually** using the ``-e`` parameter

  **Note:** Due to possible 'complications' when setting up PyPy on
  systems without pyenv, ``pypy3`` tests also will not be automatically
  run; you must invoke them manually.

* ``qa``

  Perform ``flake8`` code style checking

* ``docs``

  Builds HTML documentation using Sphinx


Environment Variables
-------------------------

``PLATFORM``
    Used on non-native-Linux operating systems to specify tests to skip.
    Valid values:

    * ``mswin`` -- when running tox on Microsoft Windows
    * ``wsl`` -- when running tox on Windows Subsystem for Linux (WSL)


Different Python Versions
-----------------------------

The tox configuration files have been created to cater for more than one
Python versions `safely`: If an interpreter is not found for a certain
Python version, tox will skip that whole testenv.

However, with a little bit of effort, you can have multiple Python interpreter
versions on your system by using ``pyenv``. General steps:

1. Install ``pyenv`` from https://github.com/pyenv/pyenv#installation

2. Install ``tox-pyenv`` from https://pypi.org/project/tox-pyenv/

3. Using ``pyenv``, install the Python versions you want to test on

4. Create a ``.python-version`` file in the root of the repo, listing the
   Python interpreter versions you want to make available to tox (see pyenv's
   documentation about this file)

5. Invoke tox with the option ``--tox-pyenv-no-fallback`` (see tox-pyenv's
   documentation about this option)


Contents
========

.. toctree::
   :maxdepth: 2

   aiosmtpd/docs/intro
   aiosmtpd/docs/concepts
   aiosmtpd/docs/cli
   aiosmtpd/docs/controller
   aiosmtpd/docs/smtp
   aiosmtpd/docs/lmtp
   aiosmtpd/docs/handlers
   aiosmtpd/docs/migrating
   aiosmtpd/docs/manpage
   aiosmtpd/docs/NEWS



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
