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

The Python standard library includes a basic |SMTP|_ server in the |smtpd|_ module,
based on the old asynchronous libraries |asyncore|_ and |asynchat|_.
These modules are quite old and are definitely showing their age;
``asyncore`` and ``asynchat`` are difficult APIs to work with, understand, extend, and fix.

With the introduction of the |asyncio|_ module in Python 3.4,
a much better way of doing asynchronous I/O is now available.
It seems obvious that an asyncio-based version of the SMTP and related protocols are needed for Python 3.
This project brings together several highly experienced Python developers collaborating on this reimplementation.

This package provides such an implementation of both the SMTP and LMTP protocols.

Full documentation is available on |aiosmtpd rtd|_


Requirements
============

You need **at least Python 3.6** to use this library.


Supported Platforms
-----------------------

``aiosmtpd`` has been tested on the following platforms (in alphabetical order):

* Cygwin (on Windows 10) [1]
* FreeBSD 12 [2]
* OpenSUSE Leap 15 [2]
* Ubuntu 18.04
* Ubuntu 20.04
* Windows 10

  | [1] Supported only with Cygwin-provided Python version
  | [2] Supported only on the latest minor release

``aiosmtpd`` *probably* can run on platforms not listed above,
but we cannot provide support for unlisted platforms.


License
=======

``aiosmtpd`` is released under the Apache License version 2.0.


Project details
===============

As of 2016-07-14, aiosmtpd has been put under the |aiolibs|_ umbrella project
and moved to GitHub.

* Project home: https://github.com/aio-libs/aiosmtpd
* PyPI project page: https://pypi.org/project/aiosmtpd/
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

This will give you a command line script called ``aiosmtpd`` which implements the
SMTP server.  Use ``aiosmtpd --help`` for details.

You will also have access to the ``aiosmtpd`` library, which you can use as a
testing environment for your SMTP clients.  See the documentation links above
for details.


Developing
==========

You'll need the `tox <https://pypi.python.org/pypi/tox>`__ tool to run the
test suite for Python 3.  Once you've got that, run::

    $ tox

Individual tests can be run like this::

    $ tox -- <testname>

where ``<testname>`` is the "node id" of the test case to run, as explained
in `the pytest documentation`_. The command above will run that one test case
against all testenvs defined in ``tox.ini`` (see below).

If you want test to stop as soon as it hit a failure, use the ``-x``/``--exitfirst``
option::

    $ tox -- -x

You can also add the ``-s``/``--capture=no`` option to show output, e.g.::

    $ tox -e py36-nocov -- -s

(The ``-e`` parameter is explained in the next section about 'testenvs'.
In general, you'll want to choose the ``nocov`` testenvs if you want to show output,
so you can see which test is generating which output.)

The `-x` and `-s` options can be combined::

    $ tox -e py36-nocov -- -x -s <testname>


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

  **Note 1:** Due to possible 'complications' when setting up PyPy on
  systems without pyenv, ``pypy3`` tests also will not be automatically
  run; you must invoke them manually. For example::

    $ tox -e pypy3-nocov

  **Note 2:** It is also possible to use whatever Python version is used when
  invoking ``tox`` by using the ``py`` target, but you must explicitly include
  the type of testing you want. For example::

    $ tox -e "py-{nocov,cov,diffcov}"

  (Don't forget the quotes if you want to use braces!)

  You might want to do this for CI platforms where the exact Python version
  is pre-prepared, such as Travis CI or |GitHub Actions|_; this will definitely
  save some time during tox's testenv prepping.

* ``qa``

  Perform ``flake8`` code style checking

* ``docs``

  Builds HTML documentation using Sphinx


Environment Variables
-------------------------

.. envvar:: PLATFORM

    Used on non-native-Linux operating systems to specify tests to skip.
    Valid values:

    +-----------+-------------------------------------------------------+
    | ``mswin`` | when running tox on Microsoft Windows (non-Cygwin)    |
    +-----------+-------------------------------------------------------+
    | ``wsl``   | when running tox on Windows Subsystem for Linux (WSL) |
    +-----------+-------------------------------------------------------+


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


``housekeep.py``
-------------------

If you ever need to 'reset' your repo, you can use the ``housekeep.py`` utility
like so::

    $ python housekeep.py superclean

It is `strongly` recommended to NOT do superclean too often, though.
Every time you invoke ``superclean``,
tox will have to recreate all its testenvs,
and this will make testing `much` longer to finish.

``superclean`` is typically only needed when you switch branches,
or if you want to really ensure that artifacts from previous testing sessions
won't interfere with your next testing sessions.


.. _`GitHub Actions`: https://docs.github.com/en/free-pro-team@latest/actions/guides/building-and-testing-python#running-tests-with-tox
.. |GitHub Actions| replace:: **GitHub Actions**
.. _`pytest doctest`: https://docs.pytest.org/en/stable/doctest.html
.. _`the pytest documentation`: https://docs.pytest.org/en/stable/usage.html#specifying-tests-selecting-tests
.. _`aiosmtpd rtd`: https://aiosmtpd.readthedocs.io
.. |aiosmtpd rtd| replace:: **aiosmtpd.readthedocs.io**
.. _`SMTP`: https://tools.ietf.org/html/rfc5321
.. |SMTP| replace:: **SMTP**
.. _`smtpd`: https://docs.python.org/3/library/smtpd.html
.. |smtpd| replace:: **smtpd**
.. _`asyncore`: https://docs.python.org/3/library/asyncore.html
.. |asyncore| replace:: ``asyncore``
.. _`asynchat`: https://docs.python.org/3/library/asynchat.html
.. |asynchat| replace:: ``asynchat``
.. _`asyncio`: https://docs.python.org/3/library/asyncio.html
.. |asyncio| replace:: ``asyncio``
.. _`aiolibs`: https://github.com/aio-libs
.. |aiolibs| replace:: **aio-libs**
