__all__ = [
    'TestEvents',
    ]


import smtplib
import unittest

from aiosmtpd.events import Debugging
from aiosmtpd.testing.helpers import Controller
from io import StringIO


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        self.controller = Controller(Debugging(self.stream))
        self.controller.start()

    def test_debugging(self):
        client = smtplib.SMTP()
        client.connect('::0', 9978)
        client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        client.docmd('EXIT')
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
