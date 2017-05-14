.. _migrating:

==================================
 Migrating from smtpd to aiosmtpd
==================================

aiosmtpd is designed to make it easy to migrate an existing application based
on `smtpd <https://docs.python.org/3/library/smtpd.html>`__ to aiosmtpd.

Consider the following subclass of ``smtpd.SMTPServer``::

    import smtpd
    import asyncore

    class CustomSMTPServer(smtpd.SMTPServer):
        def process_message(self, peer, mail_from, rcpt_tos, data):
            # Process message data...
            if error_occurred:
                return '500 Could not process your message'

    if __name__ == '__main__':
        server = CustomSMTPServer(('127.0.0.1', 10025), None)
        # Run the event loop in the current thread.
        asyncore.loop()

To switch this application to using ``aiosmtpd``, implement a handler with
the ``handle_DATA()`` method::

    import asyncio
    from aiosmtpd.controller import Controller

    class CustomHandler:
        @asyncio.coroutine
        def handle_DATA(self, server, session, envelope):
            peer = session.peer
            mail_from = envelope.mail_from
            rcpt_tos = envelope.rcpt_tos
            data = envelope.content         # type: bytes
            # Process message data...
            if error_occurred:
                return '500 Could not process your message'
            return '250 OK'

    if __name__ == '__main__':
        handler = CustomHandler()
        controller = Controller(handler, hostname='127.0.0.1', port=10025)
        # Run the event loop in a separate thread.
        controller.start()
        # Wait for the user to press Return.
        input('SMTP server running. Press Return to stop server and exit.')
        controller.stop()

Important differences to note:

* Unlike ``process_message()`` in smtpd, ``handle_DATA()`` **must** return
  an SMTP response code for the sender such as ``"250 OK"``.
* ``handle_DATA()`` must be a coroutine function, which means it must be
  declared with ``@asyncio.coroutine`` (or ``async def`` for Python 3.5 and
  newer).
* ``controller.start()`` runs the SMTP server in a separate thread and can be
  stopped again by calling ``controller.stop()``.
