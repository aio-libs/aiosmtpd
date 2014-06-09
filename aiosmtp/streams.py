"""Streaming utilities"""

__all__ = ['SmtpStreamReader']

import asyncio

from . import const
from . import errors


class SmtpStreamReader(asyncio.StreamReader):
    @asyncio.coroutine
    def read_data(self, max_len=None):
        """ Reads a dot-delimited SMTP DATA segment."""
        data_length = 0
        lines = []

        while True:
            line = yield from self.read_crlf_line(max_len=None)

            if line == const.DATA_TERM:
                break

            if line.startswith(b'.'):
                line = line[1:]

            data_length += len(line)
            if max_len and data_length > max_len:
                raise errors.TooMuchDataError()

            lines.append(line)

        result = const.LINE_TERM.join(lines)
        if max_len and len(result) >= max_len:
            raise errors.TooMuchDataError()

        return result

    @asyncio.coroutine
    def read_crlf_line(self, max_len=512):
        """Reads a <CRLF>-terminated line."""
        line = bytearray()
        not_enough = True

        while not_enough:
            while self._buffer and not_enough:
                ichar = self._buffer.find(const.LINE_TERM)
                if ichar < 0:
                    line.extend(self._buffer)
                    self._buffer.clear()
                else:
                    ichar += len(const.LINE_TERM)
                    line.extend(self._buffer[:ichar])
                    del self._buffer[:ichar]
                    not_enough = False

            if max_len and len(line) > max_len:
                raise errors.TooMuchDataError()

            if self._eof:
                break

            if not_enough:
                self._waiter = self._create_waiter('read_crlf_line')
                try:
                    yield from self._waiter
                finally:
                    self._waiter = None

        self._maybe_resume_transport()
        return bytes(line)
