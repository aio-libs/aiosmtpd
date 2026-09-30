# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

from typing import Any, List

from public import public

from aiosmtpd.smtp import MISSING, SMTP, syntax


@public
class LMTP(SMTP):
    show_smtp_greeting: bool = False

    @syntax('LHLO hostname')
    async def smtp_LHLO(self, arg: str) -> None:
        """The LMTP greeting, used instead of HELO/EHLO."""
        await super().smtp_EHLO(arg)

    async def smtp_HELO(self, arg: str) -> None:
        """HELO is not a valid LMTP command."""
        await self.push('500 Error: command "HELO" not recognized')

    async def smtp_EHLO(self, arg: str) -> None:
        """EHLO is not a valid LMTP command."""
        await self.push('500 Error: command "EHLO" not recognized')

    def _data_replies(self, status: Any) -> List[Any]:
        """One reply per accepted recipient, in RCPT order (:rfc:`2033` § 4.2).

        ``handle_DATA`` may return a list or tuple holding one status per
        recipient; a single status (or none at all) is repeated for every
        recipient.
        """
        assert self.envelope is not None
        recipients = len(self.envelope.rcpt_tos)
        if status is MISSING:
            return ['250 OK'] * recipients
        if isinstance(status, (str, bytes)):
            return [status] * recipients
        replies: List[Any] = list(status)
        if len(replies) != recipients:
            raise ValueError(
                f'handle_DATA returned {len(replies)} statuses '
                f'for {recipients} recipients'
            )
        return replies
