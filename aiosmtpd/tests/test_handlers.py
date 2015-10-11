__all__ = [
    'TestEvents',
    ]


import unittest

from aiosmtpd.handlers import Debugging
from aiosmtpd.testing.helpers import Controller
from io import StringIO
from smtplib import SMTP


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        self.controller = Controller(Debugging(self.stream))
        self.controller.start()
        self.addCleanup(self.controller.stop)

    def test_debugging(self):
        with SMTP() as client:
            client.connect('::0', 9978)
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        text = self.stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: ::1

Testing
------------ END MESSAGE ------------
""")
