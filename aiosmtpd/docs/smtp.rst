.. _smtp:

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

While behavior for common SMTP commands can be specified using :ref:`handlers
<handlers>`, more complex behavior such as adding custom SMTP commands requires
subclassing the ``SMTP`` class.

For example, let's say you wanted to add a new SMTP command called ``PING``.
All methods implementing ``SMTP`` commands are prefixed with ``smtp_``.  Here's
how you could implement this use case::

    >>> import asyncio
    >>> from aiosmtpd.smtp import SMTP as Server
    >>> class MyServer(Server):
    ...     @asyncio.coroutine
    ...     def smtp_PING(self, arg):
    ...         yield from self.push('259 Pong')

Now let's run this server in a controller::

    >>> from aiosmtpd.handlers import Sink
    >>> class MyController(Controller):
    ...     def factory(self):
    ...         return MyServer(self.handler)

    >>> controller = MyController(Sink())
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
    b'Pong'


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

Handlers can implement hooks that get called during the SMTP dialog, or in
exceptional cases.  These *handler hooks* are all called asynchronously and
they *must* return a status string, such as ``'250 OK'``.  All handler hooks
are optional and certain default behaviors are carried out by the ``SMTP``
class when a hook is omitted, but when handler hooks are defined, they may
have additional responsibilities, as described below.

All handler hooks take at least three arguments, the ``SMTP`` server instance,
:ref:`a session instance, and an envelope instance <sessions_and_envelopes>`.
Some methods take additional arguments.

The following hooks are currently defined:

``handle_HELO(server, session, envelope, hostname)``
    Called during ``HELO``.  The ``hostname`` argument is the host name given
    by the client in the ``HELO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute.

``handle_EHLO(server, session, envelope, hostname)``
    Called during ``EHLO``.  The ``hostname`` argument is the host name given
    by the client in the ``EHLO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute.  This hook may push
    additional ``250-<command>`` responses to the client by yielding from
    ``server.push(status)`` before returning ``250 OK`` as the final response.

``handle_NOOP(server, session, envelope)``
    Called during ``NOOP``.

``handle_QUIT(server, session, envelope)``
    Called during ``QUIT``.

``handle_VRFY(server, session, envelope, address)``
    Called during ``VRFY``.  The ``address`` argument is the parsed email
    address given by the client in the ``VRFY`` command.

``handle_MAIL(server, session, envelope, address, mail_options)``
    Called during ``MAIL FROM``.  The ``address`` argument is the parsed email
    address given by the client in the ``MAIL FROM`` command, and
    ``mail_options`` are any additional ESMTP mail options providing by the
    client.  If implemented, this hook must also set the
    ``envelope.mail_from`` attribute and it may extend
    ``envelope.mail_options`` (which is always a Python list).

``handle_RCPT(server, session, envelope, address, rcpt_options)``
    Called during ``RCPT TO``.  The ``address`` argument is the parsed email
    address given by the client in the ``RCPT TO`` command, and
    ``rcpt_options`` are any additional ESMTP recipient options providing by
    the client.  If implemented, this hook should append the address to
    ``envelope.rcpt_tos`` and may extend ``envelope.rcpt_options`` (both of
    which are always Python lists).

``handle_RSET(server, session, envelope)``
    Called during ``RSET``.

``handle_DATA(server, session, envelope)``
    Called during ``DATA`` after the entire message (`"SMTP content"
    <https://tools.ietf.org/html/rfc5321#section-2.3.9>`_ as described in
    RFC 5321) has been received.  The content is available on the ``envelope``
    object, but the values are dependent on whether the ``SMTP`` class was
    instantiated with ``decode_data=False`` (the default) or
    ``decode_data=True``.  In the former case, both ``envelope.content`` and
    ``envelope.original_content`` will be the content bytes (normalized
    according to the transparency rules in `RFC 5321, $4.5.2
    <https://tools.ietf.org/html/rfc5321#section-4.5.2>`_).  In the latter
    case, ``envelope.original_content`` will be the normalized bytes, but
    ``envelope.content`` will be the UTF-8 decoded string of the original
    content.

In addition to the SMTP command hooks, the following hooks can also be
implemented by handlers.  These have a different APIs, and are called
synchronously.

``handle_STARTTLS(server, session, envelope)``
    If implemented, and if SSL is supported, this method gets called
    during the TLS handshake phase of ``connection_made()``.  It should return
    True if the handshake succeeded, and False otherwise.

``handle_exception(error)``
    If implemented, this method is called when any error occurs during the
    handling of a connection (e.g. if an ``smtp_<command>()`` method raises an
    exception).  The exception object is passed in.  This method *must* return
    a status string, such as ``'542 Internal server error'``.  If the method
    returns None or raises an exception, an exception will be logged, and a 500
    code will be returned to the client.


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
