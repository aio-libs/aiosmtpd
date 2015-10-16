=======================
 The testing framework
=======================

The SMTP server can be used in a testing framework via a controller which runs
in a separate thread.  This allows the main thread to run the test driver, and
information can be passed between the SMTP thread and the main thread.

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
    X-RcptTos: bperson@example.com
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
