=========================================
 aiosmtpd - An asyncio based SMTP server
=========================================

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
3.4, a much better way of doing asynchronous IO is now available.  It seems
obvious that an asyncio-based version of the SMTP and related protocols are
needed for Python 3.  This project brings together several highly experienced
Python developers collaborating on this reimplementation.

This package provides such a implementation of both the SMTP and LMTP
protocols.


Requirements
============

You need at least Python 3.4 to use this library.  Python 3.3 might work if
you install the standalone `asyncio <https://pypi.python.org/pypi/asyncio>`__
library, but this combination is untested.


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

The best way to contact the developers is through the GitHub links above.


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
test suite for Python 3.4 and 3.5.  Once you've got that, run::

    $ tox

After tox has built the virtual environments, you can run individual tests
like this::

    $ .tox/py35/bin/python -m nose2 -vv -P <pattern>

where *<pattern>* is a Python regular expression matching a test name.


Contents
========

.. toctree::
   :maxdepth: 2

   aiosmtpd/docs/intro
   aiosmtpd/docs/controller
   aiosmtpd/docs/mailbox
   aiosmtpd/docs/NEWS



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
