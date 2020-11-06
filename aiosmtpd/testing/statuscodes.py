
class SMTP_STATUS_CODES:
    S220_READY_TLS = (220, b"Ready to start TLS")
    S250_OK = (250, b"OK")
    S530_HELO_FIRST = (503, b"Error: send HELO first")
    S530_STARTTLS_FIRST = (530, b"Must issue a STARTTLS command first")
    S554_LACK_SECURITY = (554, b"Command refused due to lack of security")
    S535_AUTH_INVALID = (535, b"5.7.8 Authentication credentials invalid")
