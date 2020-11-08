import socket

from typing import NamedTuple


class StatusCode(NamedTuple):
    code: int
    mesg: bytes


class SMTP_STATUS_CODES:
    # Please wrap the status codes in StatusCode() to help identify accidental
    # type mismatch (would be flagged on good Python code editors)
    S220_READY_TLS = StatusCode(220, b"Ready to start TLS")
    S221_BYE = StatusCode(221, b"Bye")
    S235_AUTH_SUCCESS = StatusCode(235, b"2.7.0 Authentication successful")
    S250_OK = StatusCode(250, b"OK")
    S250_FQDN = StatusCode(250, bytes(socket.getfqdn(), "utf-8"))
    S500_BAD_SYNTAX = StatusCode(500, b"Error: bad syntax")
    S501_SYNTAX_EHLO = StatusCode(501, b"Syntax: EHLO hostname")
    S501_SYNTAX_QUIT = StatusCode(501, b"Syntax: QUIT")
    S503_HELO_FIRST = StatusCode(503, b"Error: send HELO first")
    S530_STARTTLS_FIRST = StatusCode(530, b"Must issue a STARTTLS command first")
    S530_AUTH_REQUIRED = StatusCode(530, b"5.7.0 Authentication required")
    S553_MALFORMED = StatusCode(553, b"5.1.3 Error: malformed address")
    S554_LACK_SECURITY = StatusCode(554, b"Command refused due to lack of security")
    S535_AUTH_INVALID = StatusCode(535, b"5.7.8 Authentication credentials invalid")
