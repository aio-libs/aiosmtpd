import asyncio

from aiosmtpd.smtp import SMTP
from public import public


@public
class LMTP(SMTP):
    @asyncio.coroutine
    def smtp_LHLO(self, arg):
        """The LMTP greeting, used instead of HELO/EHLO."""
        yield from super().smtp_HELO(arg)

    @asyncio.coroutine
    def smtp_HELO(self, arg):
        """HELO is not a valid LMTP command."""
        yield from self.push('500 Error: command "HELO" not recognized')

    @asyncio.coroutine
    def smtp_EHLO(self, arg):
        """EHLO is not a valid LMTP command."""
        yield from self.push('500 Error: command "EHLO" not recognized')
