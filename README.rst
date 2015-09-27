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

Stay tuned for additional details.

Requirements
------------

- Python >= 3.3
- asyncio https://pypy.python.org/pypi/asyncio/0.4.1

License
-------
``aiosmtp`` is offered under the Apache 2.0 license.

Building
--------

.. code-block:: bash

    make venv
    . venv/bin/activate

Developing
----------
.. code-block:: bash

    make dev
    make test

