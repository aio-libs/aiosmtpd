import asyncio


class SMTPException(Exception):
    """
    Some handlers methods are not able to return response string and handle
    error by method return value. Raise this exception to indicate error and
    provide response code and message.
    """
    def __init__(self, code, message):
        super(SMTPException, self).__init__(code, message)
        self.code = code
        self.message = message


class SMTPAddress:
    def __init__(self, address, options):
        self.address = address
        self.options = options

    def __repr__(self):
        return '%s %r' % (self.address, self.options)


class BaseHandler:
    @asyncio.coroutine
    def process_message(self, peer, mailfrom, rcpttos, data):
        """
        Proccess message send by DATA command
        :param tuple peer: Peer hoat and port
        :param mailfrom: Sender from mail_from handler
        :param list rcpttos: Recipients from rcpt handler
        :param data: Email body
        :return str: SMTP response string
        """
        return '250 OK'

    def handle_tls_handshake(self, ssl_object, peercert, cipher):
        """
        Handle SMTP STARTTLS certificates handshake
        :param ssl_object:
        :param peercert:
        :param cipher:
        :return bool: True if successful, False if failed.
        """
        return True

    @asyncio.coroutine
    def mail_from(self, address, options):
        """
        Handle SMTP MAIL_FROM command
        :param str address: Sender email address
        :param options: address SMTP options
        :return: Sender entity to use in process message mailfrom argument
        """
        return SMTPAddress(address, options)

    @asyncio.coroutine
    def rcpt(self, address, options):
        """
        Handle SMTP RCPT command
        :param str address: Recipient email address
        :param options: address SMTP options
        :return: Recipient entity to use in process_message rcptos argument
        """
        return SMTPAddress(address, options)

    @asyncio.coroutine
    def verify(self, address):
        """
        SMTP VRFY handler
        :param address:
        :return str: SMTP response string
        """
        return '252 Cannot VRFY user, ' \
               'but will accept message and attempt delivery'

    @asyncio.coroutine
    def handle_exception(self, error):
        """
        Handle excpetions during SMTP session
        :param Exception error: Unhandled exception
        :return:
        """
        pass

    @classmethod
    def from_cli(cls, parser, *args):
        """
        Create handler by command line options
        :param parser: Command line parser
        :param args: Command line options
        :return BaseHandler: Handler instance
        """
        if len(args) > 0:
            parser.error('Handler does not accept arguments')
        return cls()
