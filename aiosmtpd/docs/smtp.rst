================
 The SMTP class
================

At the heart of this module is the ``SMTP`` class.  This class implements the
`RFC 5321 <http://www.faqs.org/rfcs/rfc5321.html>`_ Simple Mail Transport
Protocol.  Usually, you won't run an ``SMTP`` instance directly, but instead
will use a :ref:`controller <controller>` instance wrapper, providing start
and stop semantics in a subthread.

    >>> from aiosmtpd.controller import Controller

The ``SMTP`` class is itself a subclass of StreamReaderProtocol_.


Subclassing
===========

The ``SMTP`` class is designed for derivation.  You can add new ``SMTP``
methods or override existing semantics, and you can provide a *handler*
instance to react to certain events during the ``SMTP`` dialog.

For example, let's say you wanted to add a new method called ``PING`` and you
wanted to count how many times the ``RSET`` command was called.  All methods
implementing ``SMTP`` commands are prefixed with ``smtp_``.  Here's how you
could implement these use cases::

    >>> from aiosmtpd.smtp import SMTP as Server
    >>> class MyServer(Server):
    ...     def smtp_PING(self, arg):
    ...         yield from self.push('259 OK')
    ...
    ...     def smtp_RSET(self, arg):
    ...         self.event_handler.rset_calls += 1
    ...         yield from super().smtp_RSET(arg)

Now let's run this server in a controller::

    >>> from aiosmtpd.handlers import Sink
    >>> class Counter(Sink):
    ...     def __init__(self):
    ...         self.rset_calls = 0

    >>> class MyController(Controller):
    ...     def factory(self):
    ...         return MyServer(self.handler)

    >>> controller = MyController(Counter())
    >>> controller.start()
    >>> # Arrange for the controller to be stopped at the end of this doctest.
    >>> ignore = resources.callback(controller.stop)

We can now connect to this server with an ``SMTP`` client.

    >>> from smtplib import SMTP
    >>> client = SMTP(controller.hostname, controller.port)

Let's ping the server.  Since the ``PING`` command isn't an official ``SMTP``
command, we have to use the lower level interface to talk to it.

    >>> code, message = client.docmd('PING')
    >>> code
    259
    >>> message
    b'OK'

Now we can call ``RSET`` a few times and watch as the handler's counter gets
incremented.

    >>> code, message = client.rset()
    >>> controller.handler.rset_calls
    1
    >>> code, message = client.rset()
    >>> controller.handler.rset_calls
    2
    >>> code, message = client.rset()
    >>> controller.handler.rset_calls
    3


Server hooks
============

.. warning:: These methods are deprecated.  :ref:`handler hooks <hooks>`
             instead.

The ``SMTP`` server class also implements some hooks which your subclass can
override to provide additional responses.

``ehlo_hook()``
    This hook makes it possible for subclasses to return additional ``EHLO``
    responses.  This method, called *asynchronously* and taking no arguments,
    can do whatever it wants, including (most commonly) pushing new
    ``250-<command>`` responses to the client.  This hook is called just
    before the standard ``250 HELP`` which ends the ``EHLO`` response from the
    server.

``rset_hook()``
    This hook makes it possible to return additional ``RSET`` responses.  This
    method, called *asynchronously* and taking no arguments, is called just
    before the standard ``250 OK`` which ends the ``RSET`` response from the
    server.


.. _hooks:

Handler hooks
=============

Handlers can implement a hooks that get called during the ``SMTP`` dialog, or
in exceptional cases.  These *handler hooks* are all called asynchronously and
they *must* return a status string, since the default statuses are *not*
returned when a hook is defined.  Individual handlers may have additional
responsibilities to replace default behavior, as described below.

All handler hooks take at least three arguments, the ``SMTP`` server instance,
:ref:`a session instance, and an envelope instance <sessions_and_envelopes>`.
Some methods take additional arguments.

The following hooks are currently defined:

``handle_HELO(server, session, envelope, hostname)``
    Called during ``HELO``, the ``hostname`` argument is the host name given
    by the client in the ``HELO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute.

``handle_EHLO(server, session, envelope, hostname)``
    Called during ``EHLO``, the ``hostname`` argument is the host name given
    by the client in the ``EHLO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute.  This hook may push
    additional ``250-<command>`` responses to the client by yielding from
    ``server.push(status)``.

``handle_NOOP(server, session, envelope)``
    Called during ``NOOP``.

``handle_QUIT(server, session, envelope)``
    Called during ``QUIT``.

``handle_VRFY(server, session, envelope, address)``
    Called during ``VRFY``, the ``address`` argument is the parsed email
    address given by the client in the ``VRFY`` command.

``handle_MAIL(server, session, envelope, address, mail_options)``
    Called during ``MAIL FROM``, the ``address`` argument is the parsed email
    address given by the client in the ``MAIL FROM`` command, and
    ``mail_options`` are any additional ESMTP mail options providing by the
    client.  If implemented, this hook must also set the
    ``envelope.mail_from`` attribute and it may extend
    ``envelope.mail_options`` (which is always a Python list).

``handle_RCPT(server, session, envelope, address, rcpt_options)``
    Called during ``RCPT TO``, the ``address`` argument is the parsed email
    address given by the client in the ``RCPT TO`` command, and
    ``rcpt_options`` are any additional ESMTP recipient options providing by
    the client.  If implemented, this hook should append the address to
    ``envelope.rcpt_tos`` and may extend ``envelope.rcpt_options`` (both of
    which are always Python lists).

``handle_RSET(server, session, envelope)``
    Called during ``RSET``.

``handle_DATA(session, envelope)``
    Called during ``DATA`` after most processing of the data has occurred.
    Hooks can inspect the converted 

    This method is called on the handler so that
    it can process the incoming ``DATA`` bytes.  The ``session`` and
    ``envelope`` arguments are described below.  It returns the status string
    to pass back to the client.  If status is not given (e.g. it is None),
    then the string ``"250 OK"`` is used as the status.

``handle_tls_handshake(session)``
    (*optional*, *synchronous*) If implemented, and if SSL is supported, this
    handler method gets called during the TLS handshake phase of
    ``connection_made()``.  It should return a boolean which specifies whether
    the handshake failed or not.  ``session`` is an instance of the Session_
    object.

``handle_exception(error)``
    (*optional*, *synchronous*) If implemented, this method is called when any
    error occurs during the handling of a connection (e.g. if an
    ``smtp_COMMAND()`` command raises an exception).  The exception object is
    passed in.  Note that as part of the ``SMTP`` dialog, if an exception
    occurs, a 500 code will be returned to the client.



.. _sessions_and_envelopes:

Sessions and envelopes
======================

To make current and future hooks easier to write, two helper classes are
defined which provide attributes that can be of use to the
``handle_COMMAND()`` methods on the handler.  You can actually override the
use of these two classes by subclassing ``SMTP`` and defining the
``_create_session()`` and ``_create_envelope()`` methods.  Both of these
return the appropriate instance that will be used for the remainder of the
connection.  New session instances are created when new connections are made,
and new envelope instances are created at the beginning of an ``SMTP`` dialog,
or whenver a ``RSET`` command is issued.


Session
-------

``Session`` instances have the following attributes:

``peer``
    Defaulting to None, this attribute will contain the transport's socket's
    peername_ value.

``ssl``
    Defaulting to None, this attribute will contain some extra information,
    as a dictionary, from the ``asyncio.sslproto.SSLProtocol`` instance, which
    can be used to pull additional information out about the connection.  This
    attribute contains implementation-specific information so its contents may
    change, but it should roughly correspond to the information available
    `through this method`_.

``host_name``
    Defaulting to None, this attribute will contain the host name argument as
    seen by the ``HELO`` or ``EHLO`` command.

``extended_smtp``
    Defaulting to False, this flag will be True when the ``EHLO`` greeting
    was seen, indicating ESMTP_.

``loop``
    This is the asyncio event loop instance.


Envelope
--------

``Envelope`` instances have the following attributes:

``mail_from``
    Defaulting to None, this attribute holds the email address given in the
    ``MAIL FROM`` command.

``mail_options``
    Defaulting to None, this attribute contains a list of any ESMTP mail
    options provided by the client, such as those passed in by `the smtplib
    client`_.

``content``
    Defaulting to None, this attribute will contain the contents of the
    message as provided by the ``DATA`` command.  If the ``decode_data``
    parameter to the ``SMTP`` constructor was True (it defaults to False),
    then this attribute will contain the UTF-8 decoded string, otherwise it
    will contain the raw bytes.

``rcpt_tos``
    Defaulting to the empty list, this attribute will contain a list of the
    email addresses provided in the ``RCPT TO`` command.

``rcpt_options``
    Defaulting to the empty list, this attribute will contain the list of any
    recipient options provided by the client, such as those passed in by `the
    smtplib client`_.


.. _peername: https://docs.python.org/3/library/asyncio-protocol.html?highlight=peername#asyncio.BaseTransport.get_extra_info
.. _`through this method`: https://docs.python.org/3/library/asyncio-protocol.html?highlight=get_extra_info#asyncio.BaseTransport.get_extra_info
.. _ESMTP: http://www.faqs.org/rfcs/rfc1869.html
.. _`the smtplib client`: https://docs.python.org/3/library/smtplib.html#smtplib.SMTP.sendmail
.. _StreamReaderProtocol: https://docs.python.org/3/library/asyncio-stream.html#streamreaderprotocol
