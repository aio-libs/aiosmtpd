=================
 Getting started
=================

.. _cli:

Command line usage
==================

This package provides a main entry point which can be used to run the
server on the command line.  There are two ways to run the server, depending
on how the package has been installed.

You can run the server by passing it to Python directly::

    $ python3 -m aiosmtpd -n

This starts a server on localhost, port 8025 without setting the uid to
'nobody' (i.e. because you aren't running it as root).  Once you've done that,
you can connect directly to the server using your favorite command line
protocol tool.  Type the ``QUIT`` command at the server once you see the
greeting::

    % telnet localhost 8025
    Trying 127.0.0.1...
    Connected to localhost.
    Escape character is '^]'.
    220 subdivisions Python SMTP ...
    QUIT
    221 Bye
    Connection closed by foreign host.

Of course, you could use Python's smtplib_ module, or any other SMTP client to
talk to the server.  Just hit control-C at the server to stop it.

The entry point may also be installed as the ``aiosmtpd`` command, so this is
equivalent to the above ``python3`` invocation::

    $ aiosmtpd -n


Options
-------

Optional arguments include:

``-h``, ``--help``
    Show this help message and exit.

``-n``, ``--nosetuid``
    This program generally tries to setuid ``nobody``, unless this flag is
    set.  The setuid call will fail if this program is not run as root (in
    which case, use this flag).

``-c CLASSPATH``, ``--class CLASSPATH``
    Use the given class, as a Python dotted import path, as the handler class
    for SMTP events.  This class can process received messages and do other
    actions during the SMTP dialog.  Uses a debugging handler by default.

``-s SIZE``, ``--size SIZE``
    Restrict the total size of the incoming message to ``SIZE`` number of
    bytes via the RFC 1870 ``SIZE`` extension.  Defaults to 33554432 bytes.

``-u``, ``--smtputf8``
    Enable the SMTPUTF8 extension and behave as an RFC 6531 SMTP proxy.

``-d``, ``--debug``
    Increase debugging output.

``-l [HOST:PORT]``, ``--listen [HOST:PORT]``
    Optional host and port to listen on.  If the PORT part is not given, then
    port 8025 is used.  If only :PORT is given, then localhost is used for the
    hostname.  If neither are given, localhost:8025 is used.

Optional positional arguments provide additional arguments to the handler
class constructor named in the ``--class`` option.  Provide as many of these
as supported by the handler class's ``from_cli()`` class method, if provided.


.. _smtplib: https://docs.python.org/3/library/smtplib.html

.. _controller:

Programmatic usage
==================

The SMTP server can be used in a testing framework via a *controller* which
runs in a separate thread.  This allows the main thread to run the test
driver, and information can be passed between the SMTP thread and the main
thread.

For example, say you wanted to pass message objects between the SMTP thread
and the main thread.  Start by implementing a handler which derives from a
base handler that processes the incoming message data into an email Message
object.

    >>> from aiosmtpd.handlers import Message
    >>> handled_message = None
    >>> class MessageHandler(Message):
    ...     def handle_message(self, message):
    ...         global handled_message
    ...         handled_message = message

Now create a controller instance, passing in the handler, which gets called
when a new message is available.  Start the controller, which begins accepting
SMTP connections in the separate thread.

    >>> from aiosmtpd.controller import Controller
    >>> controller = Controller(MessageHandler())
    >>> controller.start()

The SMTP thread might run into errors during its setup phase; to catch this
the main thread will timeout when waiting for the SMTP server to become ready.
By default the timeout is set to 1 second but can be changed either by using
the ``AIOSMTPD_CONTROLLER_TIMEOUT`` environment variable or by passing a
different ``ready_timeout`` duration to the Controller's constructor.

Connect to the server...

    >>> from smtplib import SMTP
    >>> client = SMTP(controller.hostname, controller.port)
    >>> client.sendmail('aperson@example.com', ['bperson@example.com'], """\
    ... From: Anne Person <anne@example.com>
    ... To: Bart Person <bart@example.com>
    ... Subject: A test
    ... Message-ID: <ant>
    ...
    ... Hi Bart, this is Anne.
    ... """)
    {}

The message was received, and we can print it.

    >>> print(handled_message)
    From: Anne Person <anne@example.com>
    To: Bart Person <bart@example.com>
    Subject: A test
    Message-ID: <ant>
    X-Peer: ...
    X-MailFrom: aperson@example.com
    X-RcptTo: bperson@example.com
    <BLANKLINE>
    Hi Bart, this is Anne.

When you're done with the SMTP server, stop it via the controller.

    >>> controller.stop()

The server is guaranteed to be stopped.

    >>> import socket
    >>> client.connect(controller.hostname, controller.port)
    Traceback (most recent call last):
    ...
    ConnectionRefusedError: ...


The aiosmtpd library contains :ref:`base handler classes <handlers>` that may
be used to quickly gain common functionality such as parsing the incoming mail
data into an instance of ``email.message.Message``.

For a full overview of the methods that handler classes may implement,
see :ref:`Handler hooks <hooks>`.


Enable SMTPUTF8
---------------

It's very common to want to enable the ``SMTPUTF8`` ESMTP option, therefore
this is the default for the ``Controller`` constructor.  For backward
compatibility reasons, this is *not* the default for the ``SMTP`` class
though.  If you want to disable this in the ``Controller``, you can pass this
argument into the constructor::

    >>> from aiosmtpd.handlers import Sink
    >>> controller = Controller(Sink(), enable_SMTPUTF8=False)
    >>> controller.start()

    >>> client = SMTP(controller.hostname, controller.port)
    >>> code, message = client.ehlo('me')
    >>> code
    250

The EHLO response does not include the ``SMTPUTF8`` ESMTP option.  We have to
skip the server host name line, since that's variable.

    >>> lines = message.decode('utf-8').splitlines()
    >>> for line in lines[1:]:
    ...     print(line)
    SIZE 33554432
    8BITMIME
    HELP

    >>> controller.stop()
