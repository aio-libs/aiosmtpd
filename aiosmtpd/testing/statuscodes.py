import socket


class SMTP_STATUS_CODES:
    S220_READY_TLS = (220, b"Ready to start TLS")
    S221_BYE = (221, b"Bye")
    S235_AUTH_SUCCESS = (235, b"2.7.0 Authentication successful")
    S250_OK = (250, b"OK")
    S250_FQDN = (250, bytes(socket.getfqdn(), "utf-8"))
    S500_BAD_SYNTAX = (500, b"Error: bad syntax")
    S501_SYNTAX_EHLO = (501, b"Syntax: EHLO hostname")
    S501_SYNTAX_QUIT = (501, b"Syntax: QUIT")
    S503_HELO_FIRST = (503, b"Error: send HELO first")
    S530_STARTTLS_FIRST = (530, b"Must issue a STARTTLS command first")
    S530_AUTH_REQUIRED = (530, b"5.7.0 Authentication required")
    S553_MALFORMED = (553, b"5.1.3 Error: malformed address")
    S554_LACK_SECURITY = (554, b"Command refused due to lack of security")
    S535_AUTH_INVALID = (535, b"5.7.8 Authentication credentials invalid")
