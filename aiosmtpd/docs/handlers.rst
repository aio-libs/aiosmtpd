.. _handlers:

==========
 Handlers
==========

Handlers are classes which can implement :ref:`hook methods <hooks>` that get
called at various points in the SMTP dialog.  Handlers can also be named on
the :ref:`command line <cli>`, but if the class's constructor takes arguments,
you must define a ``@classmethod`` that converts the positional arguments and
returns a handler instance:

``from_cli(cls, parser, *args)``
    Convert the positional arguments, as strings passed in on the command
    line, into a handler instance.  ``parser`` is the ArgumentParser_ instance
    in use.

If ``from_cli()`` is not defined, the handler can still be used on the command
line, but its constructor cannot accept arguments.


.. _hooks:

Handler hooks
=============

Handlers can implement hooks that get called during the SMTP dialog, or in
exceptional cases.  These *handler hooks* are all called asynchronously
(i.e. they are coroutines) and they *must* return a status string, such as
``'250 OK'``.  All handler hooks are optional and default behaviors are
carried out by the ``SMTP`` class when a hook is omitted, so you only need to
implement the ones you care about.  When a handler hook is defined, it may
have additional responsibilities as described below.

All handler hooks take at least three arguments, the ``SMTP`` server instance,
:ref:`a session instance, and an envelope instance <sessions_and_envelopes>`.
Some methods take additional arguments.

The following hooks are currently defined:

``handle_HELO(server, session, envelope, hostname)``
    Called during ``HELO``.  The ``hostname`` argument is the host name given
    by the client in the ``HELO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute before returning
    ``'250 {}'.format(server.hostname)`` as the status.

``handle_EHLO(server, session, envelope, hostname)``
    Called during ``EHLO``.  The ``hostname`` argument is the host name given
    by the client in the ``EHLO`` command.  If implemented, this hook must
    also set the ``session.host_name`` attribute.  This hook may push
    additional ``250-<command>`` responses to the client by yielding from
    ``server.push(status)`` before returning ``250 HELP`` as the final
    response.

``handle_NOOP(server, session, envelope, arg)``
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
    according to the transparency rules in |RFC 5321, §4.5.2|_).  In the latter
    case, ``envelope.original_content`` will be the normalized bytes, but
    ``envelope.content`` will be the UTF-8 decoded string of the original
    content.

``handle_AUTH(server, session, envelope, args)``
    Called to handle ``AUTH`` command, if you need custom AUTH behavior.
    You *must* comply with |RFC 4954|_.
    Most of the time, you don't *need* to implement this hook;
    :ref:`AUTH hooks <auth_hooks>` are provided to override/implement selctive
    SMTP AUTH mechanisms (see below).

    ``args`` will contain the list of words following the ``AUTH`` command.
    You will need to call some ``server`` methods and modify some ``session``
    properties. ``envelope`` is usually ignored.

In addition to the SMTP command hooks, the following hooks can also be
implemented by handlers.  These have different APIs, and are called
synchronously (i.e. they are **not** coroutines).

``handle_STARTTLS(server, session, envelope)``
    If implemented, and if SSL is supported, this method gets called
    during the TLS handshake phase of ``connection_made()``.  It should return
    True if the handshake succeeded, and False otherwise.

``handle_exception(error)``
    If implemented, this method is called when any error occurs during the
    handling of a connection (e.g. if an ``smtp_<command>()`` method raises an
    exception).  The exception object is passed in.  This method *must* return
    a status string, such as ``'542 Internal server error'``.  If the method
    returns ``None`` or raises an exception, an exception will be logged, and a
    ``500`` code will be returned to the client.


.. _auth_hooks:

AUTH hooks
=============

In addition to the above SMTP hooks, you can also implement AUTH hooks.
**These hooks are asynchronous**.
Every AUTH hook is named ``auth_MECHANISM`` where ``MECHANISM`` is the all-uppercase
mechanism that the hook will implement. AUTH hooks will be called with the SMTP
server instance and a list of str following the ``AUTH`` command.

The SMTP class provides built-in AUTH hooks for the ``LOGIN`` and ``PLAIN``
mechanisms, named ``auth_LOGIN`` and ``auth_PLAIN``, respectively.
If the handler class implements ``auth_LOGIN`` and/or ``auth_PLAIN``, then
those methods of the handler instance will override the built-in methods.

``auth_MECHANISM(server: SMTP, args: List[str])``

  *server* is the instance of the ``SMTP`` class invoking the AUTH hook.
  This allows the AUTH hook implementation to invoke facilities such as the
  ``push()`` and ``_auth_interact()`` methods.

  *args* is a list of string split from the string after the ``AUTH`` command.
  ``args[0]`` is always equal to ``MECHANISM``.

  The AUTH hook **must** perform the actual validation of AUTH credentials.
  In the built-in AUTH hooks, this is done by invoking the function specified
  by the ``auth_callback`` initialization argument. AUTH hooks in handlers
  are NOT required to do the same.

  The AUTH hook **must** return one of the following values:

    * ``None`` -- an error happened during AUTH exchange/procedure, and has
      been handled inside the hook. ``smtp_AUTH`` will not do anything more.

    * ``MISSING`` -- no error during exchange, but the credentials received
      are invalid/rejected. (``MISSING`` is a pre-instantiated object you
      can import from ``aiosmtpd.smtp``)

    * *Anything else* -- an 'identity' of the STMP user. Usually is the username
      given during AUTH exchange/procedure, but not necessarily so; can also
      be, for instance, a Session ID. This will be stored in the Session
      object's ``login_data`` property (see
      :ref:`Session and Envelopes <sessions_and_envelopes>`)

**NOTE:** Defining *additional* AUTH hooks in your handler will NOT disable
the built-in LOGIN and PLAIN hooks; if you do not want to offer the LOGIN and
PLAIN mechanisms, specify them in the ``auth_exclude_mechanism`` parameter
of the :ref:`SMTP class<smtp_api>`.


Built-in handlers
=================

The following built-in handlers can be imported from ``aiosmtpd.handlers``:

* ``Debugging`` - this class prints the contents of the received messages to a
  given output stream.  Programmatically, you can pass the stream to print to
  into the constructor.  When specified on the command line, the positional
  argument must either be the string ``stdout`` or ``stderr`` indicating which
  stream to use.

* ``Proxy`` - this class is a relatively simple SMTP proxy; it forwards
  messages to a remote host and port.  The constructor takes the host name and
  port as positional arguments.  This class cannot be used on the command
  line.

* ``Sink`` - this class just consumes and discards messages.  It's essentially
  the "no op" handler.  It can be used on the command line, but accepts no
  positional arguments.

* ``Message`` - this class is a base class (it must be subclassed) which
  converts the message content into a message instance.  The class used to
  create these instances can be passed to the constructor, and defaults to
  `email.message.Message`_.

  This message instance gains a few additional headers (e.g. ``X-Peer``,
  ``X-MailFrom``, and ``X-RcptTo``).  You can override this behavior by
  overriding the ``prepare_message()`` method, which takes a session and an
  envelope.  The message instance is then passed to the handler's
  ``handle_message()`` method.  It is this method that must be implemented in
  the subclass.  ``prepare_message()`` and ``handle_message()`` are both
  called *synchronously*.  This handler cannot be used on the command line.

* ``AsyncMessage`` - a subclass of the ``Message`` handler, with the only
  difference being that ``handle_message()`` is called *asynchronously*.  This
  handler cannot be used on the command line.

* ``Mailbox`` - a subclass of the ``Message`` handler which adds the messages
  to a Maildir_.  See below for details.


The Mailbox handler
===================

A convenient handler is the ``Mailbox`` handler, which stores incoming
messages into a maildir::

    >>> import os
    >>> from aiosmtpd.controller import Controller
    >>> from aiosmtpd.handlers import Mailbox
    >>> from tempfile import TemporaryDirectory
    >>> # Clean up the temporary directory at the end of this doctest.
    >>> tempdir = resources.enter_context(TemporaryDirectory())

    >>> maildir_path = os.path.join(tempdir, 'maildir')
    >>> controller = Controller(Mailbox(maildir_path))
    >>> controller.start()
    >>> # Arrange for the controller to be stopped at the end of this doctest.
    >>> ignore = resources.callback(controller.stop)

Now we can connect to the server and send it a message...

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

...and a second message...

    >>> client.sendmail('cperson@example.com', ['dperson@example.com'], """\
    ... From: Cate Person <cate@example.com>
    ... To: Dave Person <dave@example.com>
    ... Subject: A test
    ... Message-ID: <bee>
    ...
    ... Hi Dave, this is Cate.
    ... """)
    {}

...and a third message.

    >>> client.sendmail('eperson@example.com', ['fperson@example.com'], """\
    ... From: Elle Person <elle@example.com>
    ... To: Fred Person <fred@example.com>
    ... Subject: A test
    ... Message-ID: <cat>
    ...
    ... Hi Fred, this is Elle.
    ... """)
    {}

We open up the mailbox again, and all three messages are waiting for us.

    >>> from mailbox import Maildir
    >>> from operator import itemgetter
    >>> mailbox = Maildir(maildir_path)
    >>> messages = sorted(mailbox, key=itemgetter('message-id'))
    >>> for message in messages:
    ...     print(message['Message-ID'], message['From'], message['To'])
    <ant> Anne Person <anne@example.com> Bart Person <bart@example.com>
    <bee> Cate Person <cate@example.com> Dave Person <dave@example.com>
    <cat> Elle Person <elle@example.com> Fred Person <fred@example.com>



.. _ArgumentParser: https://docs.python.org/3/library/argparse.html#argumentparser-objects
.. _`email.message.Message`: https://docs.python.org/3/library/email.compat32-message.html#email.message.Message
.. _Maildir: https://docs.python.org/3/library/mailbox.html#maildir
.. _RFC 4954: https://tools.ietf.org/html/rfc4954
.. |RFC 4954| replace:: **RFC 4954**
.. _RFC 5321, §4.5.2: https://tools.ietf.org/html/rfc5321#section-4.5.2
.. |RFC 5321, §4.5.2| replace:: **RFC 5321, §4.5.2**
