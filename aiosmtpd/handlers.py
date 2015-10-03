"""Handlers which provide custom processing at various events.

At certain times in the SMTP protocol, various events can be processed.  These
events include the SMTP commands, and at the completion of the data receipt.
Pass in an instance of one of these classes, or derive your own, to provide
your own handling of messages.  Implement only the methods you care about.
"""


__all__ = [
    'Base',
    'Debugging',
    'Proxy',
    ]


import logging

NEWLINE = '\n'

log = logging.getLogger('mail.debug')


class Base:
    # API for "doing something useful with the message"
    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        """Override this abstract method to handle messages from the client.

        peer is a tuple containing (ipaddr, port) of the client that made the
        socket connection to our smtp port.

        mailfrom is the raw address the client claims the message is coming
        from.

        rcpttos is a list of raw addresses the client wishes to deliver the
        message to.

        data is a string containing the entire full text of the message,
        headers (if supplied) and all.  It has been `de-transparencied'
        according to RFC 821, Section 4.5.2.  In other words, a line
        containing a `.' followed by other text has had the leading dot
        removed.

        kwargs is a dictionary containing additional information. It is empty
        unless decode_data=False or enable_SMTPUTF8=True was given as init
        parameter, in which case ut will contain the following keys:
            'mail_options': list of parameters to the mail command.  All
                            elements are uppercase strings.  Example:
                            ['BODY=8BITMIME', 'SMTPUTF8'].
            'rcpt_options': same, for the rcpt command.

        This function should return None for a normal `250 Ok' response;
        otherwise, it should return the desired response string in RFC 821
        format.

        """
        raise NotImplementedError


class Debugging:
    def _print_message_content(self, peer, data):
        inheaders = 1
        lines = data.splitlines()
        for line in lines:
            # headers first
            if inheaders and not line:
                peerheader = 'X-Peer: ' + peer[0]
                if not isinstance(data, str):
                    # decoded_data=false; make header match other binary output
                    peerheader = repr(peerheader.encode('utf-8'))
                print(peerheader)
                inheaders = 0
            if not isinstance(data, str):
                # Avoid spurious 'str on bytes instance' warning.
                line = repr(line)
            print(line)

    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        print('---------- MESSAGE FOLLOWS ----------')
        if kwargs:
            if kwargs.get('mail_options'):
                print('mail options: %s' % kwargs['mail_options'])
            if kwargs.get('rcpt_options'):
                print('rcpt options: %s\n' % kwargs['rcpt_options'])
        self._print_message_content(peer, data)
        print('------------ END MESSAGE ------------')


class Proxy:
    def process_message(self, peer, mailfrom, rcpttos, data):
        lines = data.split('\n')
        # Look for the last header
        i = 0
        for line in lines:
            if not line:
                break
            i += 1
        lines.insert(i, 'X-Peer: %s' % peer[0])
        data = NEWLINE.join(lines)
        refused = self._deliver(mailfrom, rcpttos, data)
        # TBD: what to do with refused addresses?
        log.info('we got some refusals: %s', refused)

    def _deliver(self, mailfrom, rcpttos, data):
        import smtplib
        refused = {}
        try:
            s = smtplib.SMTP()
            s.connect(self._remoteaddr[0], self._remoteaddr[1])
            try:
                refused = s.sendmail(mailfrom, rcpttos, data)
            finally:
                s.quit()
        except smtplib.SMTPRecipientsRefused as e:
            log.info('got SMTPRecipientsRefused')
            refused = e.recipients
        except (OSError, smtplib.SMTPException) as e:
            log.exception('got', e.__class__)
            # All recipients were refused.  If the exception had an associated
            # error code, use it.  Otherwise,fake it with a non-triggering
            # exception code.
            errcode = getattr(e, 'smtp_code', -1)
            errmsg = getattr(e, 'smtp_error', 'ignore')
            for r in rcpttos:
                refused[r] = (errcode, errmsg)
        return refused
