"""Handlers which provide custom processing at various events.

At certain times in the SMTP protocol, various events can be processed.  These
events include the SMTP commands, and at the completion of the data receipt.
Pass in an instance of one of these classes, or derive your own, to provide
your own handling of messages.  Implement only the methods you care about.
"""


__all__ = [
    'Debugging',
    'Mailbox',
    'Message',
    'Proxy',
    'Sink',
    ]


import sys
import logging
import mailbox

from email import message_from_bytes, message_from_string


COMMASPACE = ', '
NEWLINE = '\n'
log = logging.getLogger('mail.debug')


class Debugging:
    def __init__(self, stream=None):
        self.stream = sys.stdout if stream is None else stream

    @classmethod
    def from_cli(cls, parser, *args):
        error = False
        stream = None
        if len(args) == 0:
            pass
        elif len(args) > 1:
            error = True
        elif args[0] == 'stdout':
            stream = sys.stdout
        elif args[0] == 'stderr':
            stream = sys.stderr
        else:
            error = True
        if error:
            parser.error('Debugging usage: [stdout|stderr]')
        return cls(stream)

    def _print_message_content(self, peer, data):
        in_headers = True
        for line in data.splitlines():
            # Dump the RFC 2822 headers first.
            if in_headers and not line:
                peerheader = 'X-Peer: ' + peer[0]
                if not isinstance(data, str):
                    # decoded_data=false; make header match other binary output
                    peerheader = repr(peerheader.encode('utf-8'))
                print(peerheader, file=self.stream)
                in_headers = False
            if not isinstance(data, str):
                # Avoid spurious 'str on bytes instance' warning.
                line = repr(line)
            print(line, file=self.stream)

    def process_message(self, peer, mailfrom, rcpttos, data, **kws):
        print('---------- MESSAGE FOLLOWS ----------', file=self.stream)
        if kws:
            if kws.get('mail_options'):
                print('mail options: %s' % kws['mail_options'],
                      file=self.stream)
            if kws.get('rcpt_options'):
                print('rcpt options: %s\n' % kws['rcpt_options'],
                      file=self.stream())
        self._print_message_content(peer, data)
        print('------------ END MESSAGE ------------', file=self.stream)


class Proxy:
    def process_message(self, peer, mailfrom, rcpttos, data, **kws):
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


class Sink:
    @classmethod
    def from_cli(cls, parser, *args):
        if len(args) > 0:
            parser.error('Sink handler does not accept arguments')
        return cls()

    def process_message(self, peer, mailfrom, rcpttos, data, **kws):
        pass                                        # pragma: no cover


class Message:
    def __init__(self, message_class=None):
        self.message_class = message_class

    def process_message(self, peer, mailfrom, rcpttos, data, **kws):
        # If the server was created with decode_data True, then data will be a
        # str, otherwise it will be bytes.
        if isinstance(data, bytes):
            message = message_from_bytes(data, self.message_class)
        else:
            assert isinstance(data, str), (
              'Expected str or bytes, got {}'.format(type(data)))
            message = message_from_string(data, self.message_class)
        message['X-Peer'] = str(peer)
        message['X-MailFrom'] = mailfrom
        message['X-RcptTos'] = COMMASPACE.join(rcpttos)
        self.handle_message(message)

    def handle_message(self, message):
        raise NotImplementedError                   # pragma: no cover


class Mailbox(Message):
    def __init__(self, mail_dir, message_class=None):
        self.mailbox = mailbox.Maildir(mail_dir)
        super().__init__(message_class)

    def handle_message(self, message):
        self.mailbox.add(message)

    def reset(self):
        self.mailbox.clear()
