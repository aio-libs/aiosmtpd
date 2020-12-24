import os
import asyncio
import logging
import unittest

from aiosmtpd.main import main, parseargs
from aiosmtpd.smtp import __version__
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

try:
    import pwd
except ImportError:
    pwd = None

has_setuid = hasattr(os, 'setuid')
log = logging.getLogger('mail.log')


ModuleResources = ExitStack()


def setUpModule():
    # Needed especially on FreeBSD because socket.getfqdn() is slow on that OS,
    # and oftentimes (not always, though) leads to Error
    ModuleResources.enter_context(patch("socket.getfqdn", return_value="localhost"))


def tearDownModule():
    ModuleResources.close()


class TestHandler1:
    def __init__(self, called):
        self.called = called

    @classmethod
    def from_cli(cls, parser, *args):
        return cls(*args)


class TestHandler2:
    pass


class TestMain(unittest.TestCase):
    def setUp(self):
        old_log_level = log.getEffectiveLevel()
        self.addCleanup(log.setLevel, old_log_level)
        self.resources = ExitStack()
        # Create a new event loop, and arrange for that loop to end almost
        # immediately.  This will allow the calls to main() in these tests to
        # also exit almost immediately.  Otherwise, the foreground test
        # process will hang.
        #
        # I think this introduces a race condition.  It depends on whether the
        # call_later() can possibly run before the run_forever() does, or could
        # cause it to not complete all its tasks.  In that case, you'd likely
        # get an error or warning on stderr, which may or may not cause the
        # test to fail.  I've only seen this happen once and don't have enough
        # information to know for sure.
        default_loop = asyncio.get_event_loop()
        loop = asyncio.new_event_loop()
        # The original value of 0.1 is too small; on underpowered test benches
        # (like my laptop) the initialization of the whole asyncio 'system'
        # (i.e., create_server + run_until_complete + run_forever) *sometimes*
        # takes more than 0.1 seconds, causing tests to fail intermittently
        # with “Event loop stopped before Future completed.” error.
        #
        # Because the error is intermittent and infrequently happen (maybe
        # only about 5-10% of testing attempts), I figure the actual time
        # needed would be 0.1 +/- 20%; so raising this value by 900%
        # *should* be enough. We can revisit this in the future if it needs
        # to be longer.
        #
        # Edited 2020-12-21: Started seeing same intermittent error. Increasing by
        # 50% to 1.5
        loop.call_later(1.5, loop.stop)
        self.resources.callback(asyncio.set_event_loop, default_loop)
        asyncio.set_event_loop(loop)
        self.addCleanup(self.resources.close)

    @unittest.skipIf(pwd is None, 'No pwd module available')
    def test_setuid(self):
        with patch('os.setuid') as mock:
            main(args=())
            mock.assert_called_with(pwd.getpwnam('nobody').pw_uid)

    @unittest.skipIf(pwd is None, 'No pwd module available')
    @patch('os.setuid', side_effect=PermissionError)
    @patch('sys.stderr', new_callable=StringIO)
    def test_setuid_permission_error(self, mock_err, mock_setuid):
        with self.assertRaises(SystemExit) as cm:
            main(args=())
        self.assertEqual(cm.exception.code, 1)
        mock_setuid.assert_called_with(pwd.getpwnam('nobody').pw_uid)
        self.assertEqual(
            mock_err.getvalue(),
            'Cannot setuid "nobody"; try running with -n option.\n')

    @unittest.skipIf(pwd is None, 'No pwd module available')
    @patch('aiosmtpd.main.pwd', None)  # Returns no object, so needs no arg
    @patch('sys.stderr', new_callable=StringIO)
    def test_setuid_no_pwd_module(self, mock_err):
        with self.assertRaises(SystemExit) as cm:
            main(args=())
        self.assertEqual(cm.exception.code, 1)
        # On Python 3.8 on Linux, a bunch of "RuntimeWarning: coroutine
        # 'AsyncMockMixin._execute_mock_call' was never awaited" messages
        # gets mixed up into stderr causing test fail.
        # Therefore, we use assertIn instead of assertEqual here, because
        # the string DOES appear in stderr, just buried.
        self.assertIn(
            'Cannot import module "pwd"; try running with -n option.\n',
            mock_err.getvalue(),
        )

    @unittest.skipUnless(has_setuid, 'setuid is unvailable')
    # Just to short-circuit the main() function.
    @patch('aiosmtpd.main.partial', side_effect=RuntimeError)
    @patch('os.setuid', side_effect=PermissionError)
    @patch('aiosmtpd.main.pwd', None)  # Returns no object, so needs no arg
    def test_n(self, mock_setuid, mock_partial):
        # Getting the RuntimeError means that a SystemExit was never
        # triggered in the setuid section.
        self.assertRaises(RuntimeError, main, ('-n',))

    @unittest.skipUnless(has_setuid, 'setuid is unvailable')
    # Just to short-circuit the main() function.
    @patch('aiosmtpd.main.partial', side_effect=RuntimeError)
    @patch('os.setuid', side_effect=PermissionError)
    @patch('aiosmtpd.main.pwd', None)  # Returns no object, so needs no arg
    def test_nosetuid(self, mock_setuid, mock_partial):
        # Getting the RuntimeError means that a SystemExit was never
        # triggered in the setuid section.
        self.assertRaises(RuntimeError, main, ('--nosetuid',))

    def test_debug_0(self):
        # For this test, the runner will have already set the log level so it
        # may not be logging.ERROR.
        _log = logging.getLogger('mail.log')
        default_level = _log.getEffectiveLevel()
        with patch.object(_log, 'info'):
            main(('-n',))
            self.assertEqual(_log.getEffectiveLevel(), default_level)

    def test_debug_1(self):
        # Mock the logger to eliminate console noise.
        with patch.object(logging.getLogger('mail.log'), 'info'):
            main(('-n', '-d'))
            self.assertEqual(log.getEffectiveLevel(), logging.INFO)

    # Mock the logger to eliminate console noise.
    @patch("logging.Logger.info")
    @patch("logging.Logger.debug")
    def test_debug_2(self, mock_debug, mock_info):
        main(('-n', '-dd'))
        self.assertEqual(log.getEffectiveLevel(), logging.DEBUG)

    # Mock the logger to eliminate console noise.
    @patch("logging.Logger.info")
    @patch("logging.Logger.debug")
    def test_debug_3(self, mock_debug, mock_info):
        main(('-n', '-ddd'))
        self.assertEqual(log.getEffectiveLevel(), logging.DEBUG)
        self.assertTrue(asyncio.get_event_loop().get_debug())


class TestParseArgs(unittest.TestCase):
    def test_handler_from_cli(self):
        # Ignore the host:port positional argument.
        parser, args = parseargs(
            ('-c', 'aiosmtpd.tests.test_main.TestHandler1', '--', 'FOO'))
        self.assertIsInstance(args.handler, TestHandler1)
        self.assertEqual(args.handler.called, 'FOO')

    def test_handler_no_from_cli(self):
        # Ignore the host:port positional argument.
        parser, args = parseargs(
            ('-c', 'aiosmtpd.tests.test_main.TestHandler2'))
        self.assertIsInstance(args.handler, TestHandler2)

    def test_handler_from_cli_exception(self):
        self.assertRaises(TypeError, parseargs,
                          ('-c', 'aiosmtpd.tests.test_main.TestHandler1',
                           'FOO', 'BAR'))

    def test_handler_no_from_cli_exception(self):
        stderr = StringIO()
        with patch('sys.stderr', stderr):
            with self.assertRaises(SystemExit) as cm:
                parseargs(
                    ('-c', 'aiosmtpd.tests.test_main.TestHandler2',
                     'FOO', 'BAR'))
            self.assertEqual(cm.exception.code, 2)
        usage_lines = stderr.getvalue().splitlines()
        self.assertEqual(
            usage_lines[-1][-57:],
            'Handler class aiosmtpd.tests.test_main takes no arguments')

    def test_default_host_port(self):
        parser, args = parseargs(args=())
        self.assertEqual(args.host, 'localhost')
        self.assertEqual(args.port, 8025)

    def test_l(self):
        parser, args = parseargs(args=('-l', 'foo:25'))
        self.assertEqual(args.host, 'foo')
        self.assertEqual(args.port, 25)

    def test_listen(self):
        parser, args = parseargs(args=('--listen', 'foo:25'))
        self.assertEqual(args.host, 'foo')
        self.assertEqual(args.port, 25)

    def test_host_no_port(self):
        parser, args = parseargs(args=('-l', 'foo'))
        self.assertEqual(args.host, 'foo')
        self.assertEqual(args.port, 8025)

    def test_host_no_host(self):
        parser, args = parseargs(args=('-l', ':25'))
        self.assertEqual(args.host, 'localhost')
        self.assertEqual(args.port, 25)

    def test_ipv6_host_port(self):
        parser, args = parseargs(args=('-l', '::0:25'))
        self.assertEqual(args.host, '::0')
        self.assertEqual(args.port, 25)

    def test_bad_port_number(self):
        stderr = StringIO()
        with patch('sys.stderr', stderr):
            with self.assertRaises(SystemExit) as cm:
                parseargs(('-l', ':foo'))
            self.assertEqual(cm.exception.code, 2)
        usage_lines = stderr.getvalue().splitlines()
        self.assertEqual(usage_lines[-1][-24:], 'Invalid port number: foo')

    @patch('aiosmtpd.main.PROGRAM', 'smtpd')  # does NOT return a mock object! so...
    @patch('sys.stdout', new_callable=StringIO)
    def test_version(self, mock_out):  # ...so, only one arg here
        with self.assertRaises(SystemExit) as cm:
            parseargs(('--version',))
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(mock_out.getvalue(), 'smtpd {}\n'.format(__version__))

    @patch('aiosmtpd.main.PROGRAM', 'smtpd')  # does NOT return a mock object! so...
    @patch('sys.stdout', new_callable=StringIO)
    def test_v(self, mock_out):  # ...so, only one arg here
        with self.assertRaises(SystemExit) as cm:
            parseargs(('-v',))
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(mock_out.getvalue(), 'smtpd {}\n'.format(__version__))


class TestSigint(unittest.TestCase):
    def setUp(self):
        default_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.addCleanup(asyncio.set_event_loop, default_loop)

    def test_keyboard_interrupt(self):
        """
        main() must close loop gracefully on Ctrl-C.
        """

        def interrupt():
            raise KeyboardInterrupt
        self.loop.call_later(1.5, interrupt)

        try:
            main(("-n",))
        except Exception:
            self.fail("main() should've closed cleanly without exceptions!")
        else:
            self.assertFalse(self.loop.is_running())
