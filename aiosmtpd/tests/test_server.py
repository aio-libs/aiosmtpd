"""Test other aspects of the server implementation."""


__all__ = [
    'TestServer',
    ]


import unittest

from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP


class TestServer(unittest.TestCase):
    def test_constructor_contraints(self):
        # These two arguments cannot both be set.
        self.assertRaises(ValueError, SMTP, Sink(),
                          enable_SMTPUTF8=True,
                          decode_data=True)
