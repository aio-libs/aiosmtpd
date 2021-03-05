.. _controller:

====================
 Programmatic usage
====================

If you already have an `asyncio event loop`_, you can `create a server`_ using
the ``SMTP`` class as the *protocol factory*, and then run the loop forever.
If you need to pass arguments to the ``SMTP`` constructor, use
:func:`functools.partial` or write your own wrapper function.  You might also
want to add a signal handler so that the loop can be stopped, say when you hit
control-C.

It's probably easier to use a *controller* which runs the SMTP server in a
separate thread with a dedicated event loop.  The controller provides useful
and reliable *start* and *stop* semantics so that the foreground thread
doesn't block.  Among other use cases, this makes it convenient to spin up an
SMTP server for unit tests.

In both cases, you need to pass a :ref:`handler <handlers>` to the ``SMTP``
constructor.  Handlers respond to events that you care about during the SMTP
dialog.


Using the controller
====================

.. _tcpserver:

TCP-based Server
----------------

The :class:`Controller` class creates a TCP-based server,
listening on an Internet endpoint (i.e., ``ip_address:port`` pair).

Say you want to receive email for ``example.com`` and print incoming mail data
to the console.  Start by implementing a handler as follows:

.. doctest::

    >>> import asyncio
    >>> class ExampleHandler:
    ...     async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
    ...         if not address.endswith('@example.com'):
    ...             return '550 not relaying to that domain'
    ...         envelope.rcpt_tos.append(address)
    ...         return '250 OK'
    ...
    ...     async def handle_DATA(self, server, session, envelope):
    ...         print('Message from %s' % envelope.mail_from)
    ...         print('Message for %s' % envelope.rcpt_tos)
    ...         print('Message data:\n')
    ...         for ln in envelope.content.decode('utf8', errors='replace').splitlines():
    ...             print(f'> {ln}'.strip())
    ...         print()
    ...         print('End of message')
    ...         return '250 Message accepted for delivery'

Pass an instance of your ``ExampleHandler`` class to the ``Controller``, and
then start it:

.. doctest::

    >>> from aiosmtpd.controller import Controller
    >>> controller = Controller(ExampleHandler())
    >>> controller.start()

The SMTP thread might run into errors during its setup phase; to catch this
the main thread will timeout when waiting for the SMTP server to become ready.
By default the timeout is set to 1 second but can be changed either by using
the :envvar:`AIOSMTPD_CONTROLLER_TIMEOUT` environment variable or by passing a
different ``ready_timeout`` duration to the Controller's constructor.

Connect to the server and send a message, which then gets printed by
``ExampleHandler``:

.. doctest::

    >>> from smtplib import SMTP as Client
    >>> client = Client(controller.hostname, controller.port)
    >>> r = client.sendmail('a@example.com', ['b@example.com'], """\
    ... From: Anne Person <anne@example.com>
    ... To: Bart Person <bart@example.com>
    ... Subject: A test
    ... Message-ID: <ant>
    ...
    ... Hi Bart, this is Anne.
    ... """)
    Message from a@example.com
    Message for ['b@example.com']
    Message data:
    <BLANKLINE>
    > From: Anne Person <anne@example.com>
    > To: Bart Person <bart@example.com>
    > Subject: A test
    > Message-ID: <ant>
    >
    > Hi Bart, this is Anne.
    <BLANKLINE>
    End of message

You'll notice that at the end of the ``DATA`` command, your handler's
``handle_DATA()`` method was called.  The sender, recipients, and message
contents were taken from the envelope, and printed at the console.  The
handler methods also returns a successful status message.

The ``ExampleHandler`` class also implements a ``handle_RCPT()`` method.  This
gets called after the ``RCPT TO`` command is sanity checked.  The method
ensures that all recipients are local to the ``@example.com`` domain,
returning an error status if not.  It is the handler's responsibility to add
valid recipients to the ``rcpt_tos`` attribute of the envelope and to return a
successful status.

Thus, if we try to send a message to a recipient not inside ``example.com``,
it is rejected:

.. doctest::

    >>> client.sendmail('aperson@example.com', ['cperson@example.net'], """\
    ... From: Anne Person <anne@example.com>
    ... To: Chris Person <chris@example.net>
    ... Subject: Another test
    ... Message-ID: <another>
    ...
    ... Hi Chris, this is Anne.
    ... """)
    Traceback (most recent call last):
    ...
    smtplib.SMTPRecipientsRefused: {'cperson@example.net': (550, b'not relaying to that domain')}

When you're done with the SMTP server, stop it via the controller.

.. doctest::

    >>> controller.stop()

The server is guaranteed to be stopped.

.. doctest::

    >>> client.connect(controller.hostname, controller.port)
    Traceback (most recent call last):
    ...
    ConnectionRefusedError: ...

There are a number of built-in :ref:`handler classes <handlers>` that you can
use to do some common tasks, and it's easy to write your own handler.  For a
full overview of the methods that handler classes may implement, see the
section on :ref:`handler hooks <hooks>`.

Unix Socket-based Server
------------------------

The :class:`UnixSocketController` class creates a server listening to
a Unix Socket (i.e., a special file that can act as a 'pipe' for interprocess
communication).

Usage is identical with the example described in the :ref:`tcpserver` section above,
with some differences:

**Rather than specifying a hostname:port to listen on, you specify the Socket's filepath:**

.. doctest:: unix_socket
    :skipif: in_win32 or in_cygwin

    >>> from aiosmtpd.controller import UnixSocketController
    >>> from aiosmtpd.handlers import Sink
    >>> controller = UnixSocketController(Sink(), unix_socket="smtp_socket~")
    >>> controller.start()

**Rather than connecting to IP:port, you connect to the Socket file.**
Python's :class:`smtplib.SMTP` sadly cannot connect to a Unix Socket,
so we need to handle it on our own here:

.. doctest:: unix_socket
    :skipif: in_win32 or in_cygwin

    >>> import socket
    >>> sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    >>> sock.connect("smtp_socket~")
    >>> resp = sock.recv(1024)
    >>> resp[0:4]
    b'220 '

Try sending something, don't forget to end with ``"\r\n"``:

.. doctest:: unix_socket
    :skipif: in_win32 or in_cygwin

    >>> sock.send(b"HELO example.org\r\n")
    18
    >>> resp = sock.recv(1024)
    >>> resp[0:4]
    b'250 '

And close everything when done:

.. doctest:: unix_socket
    :skipif: in_win32 or in_cygwin

    >>> sock.send(b"QUIT\r\n")
    6
    >>> resp = sock.recv(1024)
    >>> resp[0:4]
    b'221 '
    >>> sock.close()
    >>> controller.stop()


.. _enablesmtputf8:

Enabling SMTPUTF8
=================

It's very common to want to enable the ``SMTPUTF8`` ESMTP option, therefore
this is the default for the ``Controller`` constructor.  For backward
compatibility reasons, this is *not* the default for the ``SMTP`` class
though.  If you want to disable this in the ``Controller``, you can pass this
argument into the constructor:

.. doctest::

    >>> from aiosmtpd.handlers import Sink
    >>> controller = Controller(Sink(), enable_SMTPUTF8=False)
    >>> controller.start()
    >>>
    >>> client = Client(controller.hostname, controller.port)
    >>> code, message = client.ehlo('me')
    >>> code
    250

The EHLO response does not include the ``SMTPUTF8`` ESMTP option.

.. doctest::

    >>> lines = message.decode('utf-8').splitlines()
    >>> # Don't print the server host name line, since that's variable.
    >>> for line in lines[1:]:
    ...     print(line)
    SIZE 33554432
    8BITMIME
    HELP

Stop the controller if we're done experimenting:

.. doctest::

    >>> controller.stop()


Controller API
==============

.. py:module:: aiosmtpd.controller

.. class:: IP6_IS

   .. py:attribute:: NO
      :type: set

      Contains constants from :mod:`errno` that will be raised by `socket.bind()`
      if IPv6 is not available on the system.

      .. important::

         If your system does not have IPv6 support but :func:`get_localhost`
         raises an error instead of returning ``"127.0.0.1"``,
         you can add the error number into this attribute.

   .. py:attribute:: YES
      :type: set

      Contains constants from :mod:`errno` that will be raised by `socket.bind()`
      if IPv6 is not available on the system.

.. py:function:: get_localhost

   :return: The numeric address of the loopback interface; ``"::1"`` if IPv6 is supported,
      ``"127.0.0.1"`` if IPv6 is not supported.
   :rtype: str

.. class:: BaseThreadedController(\
   handler, \
   loop=None, \
   *, \
   ready_timeout, \
   ssl_context=None, \
   server_hostname=None, server_kwargs=None, **SMTP_parameters)

   :param handler: Handler object
   :param loop: The asyncio event loop in which the server will run.
      If not given, :func:`asyncio.new_event_loop` will be called to create the event loop.
   :param ready_timeout: How long to wait until server starts.
      The :envvar:`AIOSMTPD_CONTROLLER_TIMEOUT` takes precedence over this parameter.
      See :attr:`ready_timeout` for more information.
   :type ready_timeout: float
   :param ssl_context: SSL Context to wrap the socket in.
       Will be passed-through to  :meth:`~asyncio.loop.create_server` method
   :type ssl_context: ssl.SSLContext
   :param server_hostname: Server's hostname,
      will be passed-through as ``hostname`` parameter of :class:`~aiosmtpd.smtp.SMTP`
   :type server_hostname: Optional[str]
   :param server_kwargs: (DEPRECATED) A dict that
     will be passed-through as keyword arguments of :class:`~aiosmtpd.smtp.SMTP`.
     Explicitly listed keyword arguments going into ``**SMTP_parameters``
     will take precedence over this parameter
   :type server_kwargs: Dict[str, Any]
   :param SMTP_parameters: Optional keyword arguments that
     will be passed-through as keyword arguments of :class:`~aiosmtpd.smtp.SMTP`

   .. important::

      Usually, setting the ``ssl_context`` parameter will switch the protocol to ``SMTPS`` mode,
      implying unconditional encryption of the connection,
      and preventing the use of the ``STARTTLS`` mechanism.

      Actual behavior depends on the subclass's implementation.

   |
   | :part:`Attributes`

   .. attribute:: handler
      :noindex:

      The instance of the event *handler* passed to the constructor.

   .. attribute:: loop
      :noindex:

      The event loop being used.

   .. attribute:: ready_timeout
      :type: float

      The timeout value used to wait for the server to start.

      This will either be the value of
      the :envvar:`AIOSMTPD_CONTROLLER_TIMEOUT` environment variable (converted to float),
      or the :attr:`ready_timeout` parameter.

      Setting this to a high value will NOT slow down controller startup,
      because it's a timeout limit rather than a sleep delay.
      However, you may want to reduce the default value to something 'just enough'
      so you don't have to wait too long for an exception, if problem arises.

      If this timeout is breached, a :class:`TimeoutError` exception will be raised.

   .. attribute:: server

      This is the server instance returned by
      :meth:`_create_server` after the server has started.

   .. py:attribute:: smtpd
      :type: aiosmtpd.smtp.SMTP

      The server instance (of class SMTP) created by :meth:`factory` after
      the controller is started.

   |
   | :part:`Methods`

   .. py:method:: _create_server() -> Coroutine
      :abstractmethod:

      This method will be called by :meth:`_run` during :meth:`start` procedure.

      It must return a ``Coroutine`` object which will be executed by the asyncio event loop.

   .. py:method:: _trigger_server() -> None
      :abstractmethod:

      The :meth:`asyncio.loop.create_server` method (or its parallel)
      invokes :meth:`factory` "lazily",
      so exceptions in :meth:`factory` can go undetected during :meth:`start`.

      This method will create a connection to the started server and 'exchange' some traffic,
      thus triggering :meth:`factory` invocation,
      allowing the Controller to catch exceptions during initialization.

   .. method:: start() -> None

      :raises TimeoutError: if the server takes too long to get ready,
         exceeding the ``ready_timeout`` parameter.
      :raises RuntimeError: if an unrecognized & unhandled error happened,
         resulting in non-creation of a server object
         (:attr:`smtpd` remains ``None``)

      Start the server in the subthread.
      The subthread is always a :class:`daemon thread <threading.Thread>`
      (i.e., we always set ``thread.daemon=True``).

      Exceptions can be raised
      if the server does not start within :attr:`ready_timeout` seconds,
      or if any other exception occurs in :meth:`factory` while creating the server.

      .. important::

         If :meth:`start` raises an Exception,
         cleanup is not performed automatically,
         to support deep inspection post-exception (if you wish to do so.)
         Cleanup must still be performed manually by calling :meth:`stop`

         For example::

             # Assume SomeController is a concrete subclass of BaseThreadedController
             controller = SomeController(handler)
             try:
                 controller.start()
             except ...:
                 ... exception handling and/or inspection ...
             finally:
                 controller.stop()

   .. method:: stop() -> None

      :raises AssertionError: if :meth:`stop` is called before :meth:`start` is called successfully

      Stop the server and the event loop, and cancel all tasks.

   .. method:: factory() -> aiosmtpd.smtp.SMTP

      You can override this method to create custom instances of the ``SMTP``
      class being controlled.

      By default, this creates an ``SMTP`` instance,
      passing in your handler and setting flags from the :attr:`**SMTP_Parameters` parameter.

      Examples of why you would want to override this method include
      creating an :ref:`LMTP <LMTP>` server instance instead of the standard ``SMTP`` server.



.. class:: Controller(\
   handler, \
   hostname=None, port=8025, \
   loop=None, \
   *, \
   ready_timeout=3.0, \
   ssl_context=None, \
   server_hostname=None, server_kwargs=None, **SMTP_parameters)

   :param hostname: Will be given to the event loop's :meth:`~asyncio.loop.create_server` method
      as the ``host`` parameter, with a slight processing (see below)
   :type hostname: Optional[str]
   :param port: Will be passed-through to  :meth:`~asyncio.loop.create_server` method
   :type port: int

   .. note::

      The ``hostname`` parameter will be passed to the event loop's
      :meth:`~asyncio.loop.create_server` method as the ``host`` parameter,
      :boldital:`except` ``None`` (default) will be translated to ``::1``.

        * To bind `dual-stack`_ locally, use ``localhost``.

        * To bind `dual-stack`_ on all interfaces, use ``""`` (empty string).

   .. important::

      The ``hostname`` parameter does NOT get passed through to the SMTP instance;
      if you want to give the SMTP instance a custom hostname
      (e.g., for use in HELO/EHLO greeting),
      you must pass it through the :attr:`server_hostname` parameter.

   .. important::

      Explicitly defined SMTP keyword arguments will override keyword arguments of the
      same names defined in the (deprecated) ``server_kwargs`` argument.

      >>> from aiosmtpd.handlers import Sink
      >>> controller = Controller(Sink(), timeout=200, server_kwargs=dict(timeout=400))
      >>> controller.SMTP_kwargs["timeout"]
      200

      One example is the ``enable_SMTPUTF8`` flag described in the
      :ref:`Enabling SMTPUTF8 section <enablesmtputf8>` above.

   |
   | :part:`Attributes`

   .. attribute:: hostname: str
                  port: int
      :noindex:

      The values of the *hostname* and *port* arguments.

   Other parameters, attributes, and methods are identical to :class:`BaseThreadedController`
   and thus are not repeated nor explained here.


.. class:: UnixSocketController(\
   handler, \
   unix_socket, \
   loop=None, \
   *, \
   ready_timeout=3.0, \
   ssl_context=None, \
   server_hostname=None,\
   **SMTP_parameters)

   :param unix_socket: Socket file,
      will be passed-through to :meth:`asyncio.loop.create_unix_server`
   :type unix_socket: Union[str, pathlib.Path]

   |
   | :part:`Attributes`

   .. py:attribute:: unix_socket
      :type: str

      The stringified version of the ``unix_socket`` parameter

   Other parameters, attributes, and methods are identical to :class:`BaseThreadedController`
   and thus are not repeated nor explained here.


.. _`asyncio event loop`: https://docs.python.org/3/library/asyncio-eventloop.html
.. _`create a server`: https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.AbstractEventLoop.create_server
.. _dual-stack: https://en.wikipedia.org/wiki/IPv6#Dual-stack_IP_implementation
