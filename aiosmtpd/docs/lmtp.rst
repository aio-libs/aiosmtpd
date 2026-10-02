.. _LMTP:

================
 The LMTP class
================

:rfc:`2033` defines the :boldital:`Local Mail Transport Protocol`.
In many ways, this is very similar to SMTP, but with no guarantees of queuing.
It is, in a sense, an alternative to ESMTP,
and is often used for local mail routing
(e.g. from a Mail Transport Agent to a local command or system)
where the unreliability of internet connectivity is not an issue.

The ``LMTP`` class subclasses the :class:`~aiosmtpd.smtp.SMTP` class.
It implements the ``LHLO`` command,
prohibits the use of ``HELO`` and ``EHLO``,
and replies to ``DATA`` once per recipient.


Per-recipient ``DATA`` replies
==============================

:rfc:`2033` § 4.2 requires an LMTP server to send
**one reply per recipient accepted by a ``RCPT`` command**,
in the order those commands were received --
rather than the single reply SMTP sends for the whole message.
``LMTP`` does this for you:
the status returned by ``handle_DATA``
is repeated once per recipient.

To reply differently to each recipient
-- the whole point of the protocol, since LMTP delivery is per-mailbox --
return a *list* of statuses instead,
one per recipient, in the same order:

.. code-block:: python

    class MyHandler:
        async def handle_DATA(self, server, session, envelope):
            return [
                deliver(rcpt, envelope.content)  # returns e.g. "250 OK"
                for rcpt in envelope.rcpt_tos
            ]

.. important::

    The list must have exactly as many entries as ``envelope.rcpt_tos``;
    a mismatch raises :class:`ValueError`,
    which the session reports back as a ``500`` status.

    A handler that implements ``handle_RCPT`` is responsible for appending
    accepted addresses to ``envelope.rcpt_tos`` itself
    (see :ref:`the RCPT hook <hooks>`);
    recipients it never appends are not replied to.
