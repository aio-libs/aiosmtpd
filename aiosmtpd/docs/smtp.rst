.. _smtp:

================
 The SMTP class
================

At the heart of this module is the ``SMTP`` class.  This class implements the
`RFC 5321 <http://www.faqs.org/rfcs/rfc5321.html>`_ Simple Mail Transport
Protocol.  Often you won't run an ``SMTP`` instance directly, but instead will
use a :ref:`controller <controller>` instance to run the server in a subthread.

    >>> from aiosmtpd.controller import Controller

The ``SMTP`` class is itself a subclass of StreamReaderProtocol_.


.. _subclass:

Subclassing
===========

While behavior for common SMTP commands can be specified using :ref:`handlers
<handlers>`, more complex specializations such as adding custom SMTP commands
require subclassing the ``SMTP`` class.

For example, let's say you wanted to add a new SMTP command called ``PING``.
All methods implementing ``SMTP`` commands are prefixed with ``smtp_``; they
must also be coroutines.  Here's how you could implement this use case::

    >>> import asyncio
    >>> from aiosmtpd.smtp import SMTP as Server
    >>> class MyServer(Server):
    ...     async def smtp_PING(self, arg):
    ...         await self.push('259 Pong')

Now let's run this server in a controller::

    >>> from aiosmtpd.handlers import Sink
    >>> class MyController(Controller):
    ...     def factory(self):
    ...         return MyServer(self.handler)

    >>> controller = MyController(Sink())
    >>> controller.start()

..
    >>> # Arrange for the controller to be stopped at the end of this doctest.
    >>> ignore = resources.callback(controller.stop)

We can now connect to this server with an ``SMTP`` client.

    >>> from smtplib import SMTP as Client
    >>> client = Client(controller.hostname, controller.port)

Let's ping the server.  Since the ``PING`` command isn't an official ``SMTP``
command, we have to use the lower level interface to talk to it.

    >>> code, message = client.docmd('PING')
    >>> code
    259
    >>> message
    b'Pong'


Server hooks
============

.. warning:: These methods are deprecated.  See :ref:`handler hooks <hooks>`
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


SMTP API
========

.. class:: SMTP(handler, *, data_size_limit=33554432, enable_SMTPUTF8=False, decode_data=False, hostname=None, tls_context=None, require_starttls=False, loop=None)

   *handler* is an instance of a :ref:`handler <handlers>` class.

   *data_size_limit* is the limit in number of bytes that is accepted for
   client SMTP commands.  It is returned to ESMTP clients in the ``250-SIZE``
   response.  The default is 33554432.

   *enable_SMTPUTF8* is a flag that when True causes the ESMTP ``SMTPUTF8``
   option to be returned to the client, and allows for UTF-8 content to be
   accepted.  The default is False.

   *decode_data* is a flag that when True, attempts to decode byte content in
   the ``DATA`` command, assigning the string value to the :ref:`envelope's
   <sessions_and_envelopes>` ``content`` attribute.  The default is False.

   *hostname* is the string returned in the ``220`` greeting response given to
   clients when they first connect to the server.  If not given, the system's
   fully-qualified domain name is used.

   *tls_context* and *require_starttls* are related to the ESMTP ``STARTTLS``
   command for secure connections to the server, based on `RFC 3207`_.
   *tls_context* is used as the SSL protocol context, and there is no
   default.  *tls_context* must be given and *require_starttls* must be True
   for ``STARTTLS`` to be supported.

   *loop* is the asyncio event loop to use.  If not given,
   :meth:`asyncio.new_event_loop()` is called to create the event loop.

   .. attribute:: event_handler

      The *handler* instance passed into the constructor.

   .. attribute:: data_size_limit

      The value of the *data_size_limit* argument passed into the constructor.

   .. attribute:: enable_SMTPUTF8

      The value of the *enable_SMTPUTF8* argument passed into the constructor.

   .. attribute:: hostname

      The ``220`` greeting hostname.  This will either be the value of the
      *hostname* argument passed into the constructor, or the system's fully
      qualified host name.

   .. attribute:: tls_context

      The value of the *tls_context* argument passed into the constructor.

   .. attribute:: require_starttls

      True if both the *tls_context* argument to the constructor was given
      **and** the *require_starttls* flag was True.

   .. attribute:: session

      The active :ref:`session <sessions_and_envelopes>` object, if there is
      one, otherwise None.

   .. attribute:: envelope

      The active :ref:`envelope <sessions_and_envelopes>` object, if there is
      one, otherwise None.

   .. attribute:: transport

      The active `asyncio transport`_ if there is one, otherwise None.

   .. attribute:: loop

      The event loop being used.  This will either be the given *loop*
      argument, or the new event loop that was created.

   .. method:: _create_session()

      A method subclasses can override to return custom ``Session`` instances.

   .. method:: _create_envelope()

      A method subclasses can override to return custom ``Envelope`` instances.

   .. method:: push(status)

      The method that subclasses and handlers should use to return statuses to
      SMTP clients.  This is a coroutine.  *status* can be a bytes object, but
      for convenience it is more likely to be a string.  If it's a string, it
      must be ASCII, unless *enable_SMTPUTF8* is True in which case it will be
      encoded as UTF-8.

   .. method:: smtp_<COMMAND>(arg)

      Coroutine methods implementing the SMTP protocol commands.  For example,
      ``smtp_HELO()`` implements the SMTP ``HELO`` command.  Subclasses can
      override these, or add new command methods to implement custom
      extensions to the SMTP protocol.  *arg* is the rest of the SMTP command
      given by the client, or None if nothing but the command was given.


.. _StreamReaderProtocol: https://docs.python.org/3/library/asyncio-stream.html#streamreaderprotocol
.. _`RFC 3207`: http://www.faqs.org/rfcs/rfc3207.html
.. _`asyncio transport`: https://docs.python.org/3/library/asyncio-protocol.html#asyncio-transport
