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

* ``Sink`` - this class just consumes and discards messages.  It's essentiall
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
