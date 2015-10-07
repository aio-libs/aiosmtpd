"""Tests for aiosmtp/protocol.py"""

import asyncio
import functools
import unittest
import unittest.mock


class TestCaseBytesMixin:
    def assertStartsWith(self, expected, actual):
        if not actual.startswith(expected):
            msg = "Not true that {0} starts with {1}".format(
                actual, expected)
            self.fail(msg)


class AsyncTestCase(unittest.TestCase):
    """Unit test case that holds its own event loop.

    Useful in conjunction with @coro_helper.
    """
    def setUp(self):
        asyncio.set_event_loop(None)
        self._loop = asyncio.new_event_loop()

    def tearDown(self):
        self._loop.close()
        self._loop = None

    @property
    def loop(self):
        return self._loop


def coro_helper(func):
    """Decorator to mark test methods that are coroutines.

    Test methods rely on `yield from`, but the test runner doesn't
    know anything about generators or coroutines; we need to run
    tests in an event loop, specifically the one instantiated for
    each test.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwds):
        assert isinstance(args[0], AsyncTestCase), \
            "@coro_helper is only valid on AsyncTestCase methods"
        coro = asyncio.coroutine(func)
        loop = args[0].loop
        return loop.run_until_complete(coro(*args, **kwds))

    return wrapper


@unittest.skip('Ignore me')
class SmtpProtocolTests(AsyncTestCase, TestCaseBytesMixin):
    def setUp(self):
        super(SmtpProtocolTests, self).setUp()
        self.transport = unittest.mock.Mock()
        self.handler = unittest.mock.Mock()
        self.protocol = SmtpProtocol(self.handler, loop=self.loop)

        fut = asyncio.Future(loop=self.loop)
        fut.set_result(None)
        writer = unittest.mock.Mock()
        writer.drain.return_value = fut

        fut = asyncio.Future(loop=self.loop)
        fut.set_result(None)
        self.handler.message_received.return_value = fut

        self.writer = writer
        self.protocol.connection_made(self.transport)
        self.protocol.writer = self.writer

    def tearDown(self):
        super(SmtpProtocolTests, self).tearDown()

    def written_content(self):
        # Note that we skip the first mock invocation, because
        # it will always hold the server's greeting line; we
        # don't care about that for the vast majority of our
        # tests.
        return b''.join(
            [c[1][0] for c in list(self.protocol.writer.write.mock_calls[1:])])

    @coro_helper
    def test_handle_helo(self):
        self.protocol._fqdn = b'foobar.com'
        yield from self.protocol.handle_command(b'HELO getkeepsafe.com')
        self.assertStartsWith(b'250 foobar.com', self.written_content())
        self.assertEqual(b'getkeepsafe.com', self.protocol._helo)
        self.assertFalse(self.protocol.is_esmtp)

    @coro_helper
    def test_handle_duplicate_helo(self):
        self.protocol._helo = b'google.com'
        yield from self.protocol.handle_command(b'HELO getkeepsafe.com')
        self.assertStartsWith(
            b'503 Duplicate HELO/EHLO\r\n',
            self.written_content())

    @coro_helper
    def test_handle_helo_no_arg(self):
        yield from self.protocol.handle_command(b'HELO')
        self.assertStartsWith(b'501 Syntax', self.written_content())

    @coro_helper
    def test_handle_ehlo(self):
        self.protocol._fqdn = b'inbox.getkeepsafe.com'
        yield from self.protocol.handle_command(b'EHLO getkeepsafe.com')
        self.assertStartsWith(
            b'250-inbox.getkeepsafe.com',
            self.written_content())
        self.assertEqual(b'getkeepsafe.com', self.protocol._helo)
        self.assertTrue(self.protocol.is_esmtp)
        self.assertNotIn(b'SIZE', self.written_content())

    @coro_helper
    def test_handle_ehlo_with_size(self):
        self.protocol._fqdn = b'inbox.getkeepsafe.com'
        self.protocol._max_size = 1024
        yield from self.protocol.handle_command(b'EHLO getkeepsafe.com')
        self.assertStartsWith(
            b'250-inbox.getkeepsafe.com',
            self.written_content())
        self.assertIn(b'SIZE 1024', self.written_content())

    @coro_helper
    def test_handle_duplicate_ehlo(self):
        self.protocol._helo = b'gmail.com'
        yield from self.protocol.handle_command(b'EHLO getkeepsafe.com')
        self.assertStartsWith(b'503', self.written_content())
        self.assertEqual(b'gmail.com', self.protocol._helo)

    @coro_helper
    def test_handle_noop(self):
        yield from self.protocol.handle_command(b'NOOP')

        self.assertIn(b'250 Ok', self.written_content())

    @coro_helper
    def test_noop_with_args(self):
        yield from self.protocol.handle_command(b'NOOP SIZE=1024')

        self.assertIn(b'501 Syntax', self.written_content())

    @coro_helper
    def test_quit(self):
        yield from self.protocol.handle_command(b'QUIT')

        self.assertStartsWith(b'221 Ok', self.written_content())
        self.assertTrue(self.protocol.connection_closed.done())

    @coro_helper
    def test_quit_with_args(self):
        yield from self.protocol.handle_command(b'QUIT PLAYIN')
        self.assertStartsWith(b'501 Syntax', self.written_content())
        self.assertFalse(self.protocol.connection_closed.done())

    @coro_helper
    def test_expn(self):
        yield from self.protocol.handle_command(b'EXPN')
        self.assertStartsWith(b'502 Unimplemented', self.written_content())

    @coro_helper
    def test_exp_with_arg(self):
        yield from self.protocol.handle_command(b'EXPN foo')
        self.assertStartsWith(b'502 Unimplemented', self.written_content())

    @coro_helper
    def test_mail_no_helo(self):
        yield from self.protocol.handle_command(
            b'MAIL FROM: <ben@getkeepsafe.com>')
        self.assertStartsWith(b'503', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_no_arg(self):
        yield from self.protocol.handle_command(b'MAIL')
        self.assertStartsWith(b'501 Syntax', self.written_content())

    @coro_helper
    def test_mail_nested(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'natasha@gmail.com'
        yield from self.protocol.handle_command(
            b'MAIL FROM: <bgodonov@gmail.com>')
        self.assertStartsWith(b'503 Error', self.written_content())
        self.assertIn(b'Nested MAIL', self.written_content())
        self.assertEqual(b'natasha@gmail.com', self.protocol._sender)

    @coro_helper
    def test_mail_malformed_from(self):
        self.protocol._helo = b'gmail.com'
        yield from self.protocol.handle_command(
            b'MAIL FROM: Rocky Squirrel <rock@charter.net>')
        self.assertIn(b'501 Syntax', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_params_no_esmtp(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = False
        yield from self.protocol.handle_command(
            b'MAIL FROM: <rock@charter.net> SIZE=10000')
        self.assertStartsWith(b'501 Syntax', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_no_params(self):
        self.protocol._helo = b'gmail.com'
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net>')
        self.assertStartsWith(b'250 Ok', self.written_content())
        self.assertEqual(b'rock@charter.net', self.protocol._sender)

    @coro_helper
    def test_mail_params_with_esmtp(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net> SIZE=10000')
        self.assertStartsWith(b'250 Ok', self.written_content())
        self.assertEqual(b'rock@charter.net', self.protocol._sender)

    @coro_helper
    def test_mail_with_size_when_size_is_acceptable(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        self.protocol._max_size = 10000
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net> SIZE=9999')
        self.assertStartsWith(b'250 Ok', self.written_content())
        self.assertEqual(b'rock@charter.net', self.protocol._sender)

    @coro_helper
    def test_mail_with_malformed_size(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net> SIZE 10000')
        self.assertStartsWith(b'501 Syntax', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_with_size_missing_value(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net> SIZE')
        self.assertStartsWith(b'501 Syntax', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_with_size_when_size_is_too_large(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        self.protocol._max_size = 10000
        yield from self.protocol.handle_command(
            b'MAIL FROM:<rock@charter.net> SIZE=10001')
        self.assertStartsWith(b'552', self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_mail_with_unrecognized_param(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._is_esmtp = True
        yield from self.protocol.handle_command(b'MAIL FROM:<a@b.c> FOO=BAR')
        self.assertStartsWith(
            b'555 Unrecognized extension',
            self.written_content())
        self.assertEqual(None, self.protocol._sender)

    @coro_helper
    def test_rcpt_no_helo(self):
        yield from self.protocol.handle_command(b'RCPT TO:<a@b.c>')
        self.assertStartsWith(b'503 Error', self.written_content())
        self.assertEqual([], self.protocol._recipients)

    @coro_helper
    def test_rcpt_no_mail(self):
        self.protocol._helo = b'gmail.com'
        yield from self.protocol.handle_command(b'RCPT TO:<a@b.c>')
        self.assertStartsWith(b'503 Error', self.written_content())
        self.assertEqual([], self.protocol._recipients)

    @coro_helper
    def test_rcpt_no_recipient(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'x@y.z'
        yield from self.protocol.handle_command(b'RCPT TO:')
        self.assertStartsWith(b'501 Syntax', self.written_content())
        self.assertEqual([], self.protocol._recipients)

    @coro_helper
    def test_rcpt_with_param(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'x@y.z'
        yield from self.protocol.handle_command(b'RCPT TO:<a@b.c> SIZE=2')
        self.assertStartsWith(b'555', self.written_content())
        self.assertEqual([], self.protocol._recipients)

    @coro_helper
    def test_rcpt(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'x@y.z'
        yield from self.protocol.handle_command(b'RCPT TO:<a@b.c>')
        self.assertStartsWith(b'250', self.written_content())
        self.assertEqual([b'a@b.c'], self.protocol._recipients)

    @coro_helper
    def test_rcpt_address_without_angles(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'x@y.z'
        yield from self.protocol.handle_command(b'RCPT TO: a@b.c')
        self.assertStartsWith(b'250', self.written_content())
        self.assertEqual([b'a@b.c'], self.protocol._recipients)

    @coro_helper
    def test_rcpt_address_with_message_size(self):
        self.protocol._max_size = 10000
        self.protocol._message_size = 5000
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'x@y.z'
        self.protocol._recipients = [b'a@b.c']
        yield from self.protocol.handle_command(b'RCPT TO:<e@f.g>')
        self.assertStartsWith(b'552', self.written_content())
        self.assertEqual([b'a@b.c'], self.protocol._recipients)
        self.assertTrue(self.protocol._recipients_truncated)

    @coro_helper
    def test_data_no_helo(self):
        yield from self.protocol.handle_command(b'DATA')
        self.assertStartsWith(b'503', self.written_content())

    @coro_helper
    def test_data_no_sender(self):
        self.protocol._helo = b'gmail.com'
        yield from self.protocol.handle_command(b'DATA')
        self.assertStartsWith(b'503', self.written_content())

    @coro_helper
    def test_data_no_recipients(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'a@b.c'
        yield from self.protocol.handle_command(b'DATA')
        self.assertStartsWith(b'503', self.written_content())
        self.assertIn(b'Need RCPT', self.written_content())

    @coro_helper
    def test_data(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'a@b.c'
        self.protocol._recipients.append(b'x@y.z')
        self.assertEqual(0, self.protocol._read_mode)
        yield from self.protocol.handle_command(b'DATA')
        self.assertStartsWith(
            b'354 End data with <CRLF>.<CRLF>',
            self.written_content())
        self.assertEqual(1, self.protocol._read_mode)

    @coro_helper
    def test_sending_data(self):
        self.protocol._helo = b'gmail.com'
        self.protocol._sender = b'a@b.c'
        self.protocol._recipients.append(b'x@y.z')
        self.protocol._read_mode = 1
        yield from self.protocol.handle_data(b'Hello from a\r\n.\r\n')
        self.assertStartsWith(b'250', self.written_content())
        self.assertEqual(1, self.handler.message_received.call_count)
