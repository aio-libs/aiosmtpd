class SessionContext:
    peer = None
    ssl = None
    host_name = None
    extended_smtp = False


class MessageContext:
    mailfrom = None
    mail_options = None
    received_data = None

    def __init__(self):
        self.rcpttos = []
        self.rcpt_options = []
