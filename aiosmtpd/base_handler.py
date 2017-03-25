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
    def handle_DATA(self, session, envelope):
        """
        Proccess message send by DATA command
        :param Session session: Session information
        :param Envelope envelope: Email envelope
        :return str: SMTP response string
        """
        return '250 OK'

    def handle_STARTTLS(self, session):
        """
        Handle SMTP STARTTLS certificates handshake
        :param Session session: Session information
        :return bool: True if successful, False if failed.
        """
        return True

    @asyncio.coroutine
    def handle_MAIL(self, address, options):
        """
        Handle SMTP MAIL_FROM command
        :param str address: Sender email address
        :param options: address SMTP options
        :return: Sender entity to use in process message mailfrom argument
        """
        return SMTPAddress(address, options)

    @asyncio.coroutine
    def handle_RCPT(self, address, options):
        """
        Handle SMTP RCPT command
        :param str address: Recipient email address
        :param options: address SMTP options
        :return: Recipient entity to use in process_message rcptos argument
        """
        return SMTPAddress(address, options)

    @asyncio.coroutine
    def handle_VRFY(self, address):
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
