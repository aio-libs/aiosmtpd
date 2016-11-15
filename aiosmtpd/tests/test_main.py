import signal
import socket
import asyncio
import logging
import unittest

from aiosmtpd.handlers import Debugging
from aiosmtpd.main import main, parseargs, setup_sock
from aiosmtpd.smtp import SMTP
from contextlib import ExitStack
from functools import partial
from io import StringIO
from unittest.mock import call, patch

try:
    import pwd
except ImportError:
    pwd = None


log = logging.getLogger('mail.log')


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
        loop = asyncio.get_event_loop()
        self.resources = ExitStack()
        def run_forever(*args):                     # noqa: E306
            pass
        self.resources.enter_context(
            patch.object(loop, 'run_forever', run_forever))
        self.addCleanup(self.resources.close)

    @unittest.skipIf(pwd is None, 'No pwd module available')
    def test_setuid(self):
        with patch('os.setuid', side_effect=RuntimeError) as mock:
            try:
                main(args=())
            except RuntimeError:
                pass
            mock.assert_called_with(pwd.getpwnam('nobody').pw_uid)

    @unittest.skipIf(pwd is None, 'No pwd module available')
    def test_setuid_permission_error(self):
        with ExitStack() as resources:
            mock = resources.enter_context(
                patch('os.setuid', side_effect=PermissionError))
            stderr = StringIO()
            resources.enter_context(patch('sys.stderr', stderr))
            with self.assertRaises(SystemExit) as cm:
                main(args=())
            self.assertEqual(cm.exception.code, 1)
            mock.assert_called_with(pwd.getpwnam('nobody').pw_uid)
            self.assertEqual(
                stderr.getvalue(),
                'Cannot setuid "nobody"; try running with -n option.\n')

    @unittest.skipIf(pwd is None, 'No pwd module available')
    def test_setuid_no_pwd_module(self):
        with ExitStack() as resources:
            resources.enter_context(patch('aiosmtpd.main.pwd', None))
            stderr = StringIO()
            resources.enter_context(patch('sys.stderr', stderr))
            with self.assertRaises(SystemExit) as cm:
                main(args=())
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(
                stderr.getvalue(),
                'Cannot import module "pwd"; try running with -n option.\n')

    def test_n(self):
        with ExitStack() as resources:
            resources.enter_context(patch('aiosmtpd.main.pwd', None))
            resources.enter_context(
                patch('os.setuid', side_effect=PermissionError))
            # Just to short-circuit the main() function.
            resources.enter_context(
                patch('aiosmtpd.main.partial', side_effect=RuntimeError))
            # Getting the RuntimeError means that a SystemExit was never
            # triggered in the setuid section.
            self.assertRaises(RuntimeError, main, ('-n',))

    def test_nosetuid(self):
        with ExitStack() as resources:
            resources.enter_context(patch('aiosmtpd.main.pwd', None))
            resources.enter_context(
                patch('os.setuid', side_effect=PermissionError))
            # Just to short-circuit the main() function.
            resources.enter_context(
                patch('aiosmtpd.main.partial', side_effect=RuntimeError))
            # Getting the RuntimeError means that a SystemExit was never
            # triggered in the setuid section.
            self.assertRaises(RuntimeError, main, ('--nosetuid',))

    def test_debug_0(self):
        # The main loop will produce an error, but that's fine.  Also, mock
        # the logger to eliminate console noise.  For this test, the runner
        # will have already set the log level so it may not be logging.ERROR.
        log = logging.getLogger('mail.log')
        default_level = log.getEffectiveLevel()
        with patch.object(log, 'info'):
            try:
                main(('-n',))
            except RuntimeError:
                pass
            self.assertEqual(log.getEffectiveLevel(), default_level)

    def test_debug_1(self):
        # The main loop will produce an error, but that's fine.  Also, mock
        # the logger to eliminate console noise.
        with patch.object(logging.getLogger('mail.log'), 'info'):
            try:
                main(('-n', '-d'))
            except RuntimeError:
                pass
            self.assertEqual(log.getEffectiveLevel(), logging.INFO)

    def test_debug_2(self):
        # The main loop will produce an error, but that's fine.  Also, mock
        # the logger to eliminate console noise.
        with patch.object(logging.getLogger('mail.log'), 'info'):
            try:
                main(('-n', '-dd'))
            except RuntimeError:
                pass
            self.assertEqual(log.getEffectiveLevel(), logging.DEBUG)

    def test_debug_3(self):
        # The main loop will produce an error, but that's fine.  Also, mock
        # the logger to eliminate console noise.
        with patch.object(logging.getLogger('mail.log'), 'info'):
            try:
                main(('-n', '-ddd'))
            except RuntimeError:
                pass
            self.assertEqual(log.getEffectiveLevel(), logging.DEBUG)
            self.assertTrue(asyncio.get_event_loop().get_debug())


class TestLoop(unittest.TestCase):
    def setUp(self):
        # We mock out so much of this, is it even worthwhile testing?  Well, it
        # does give us coverage.
        self.loop = asyncio.get_event_loop()
        pfunc = partial(patch.object, self.loop)
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.create_server = resources.enter_context(pfunc('create_server'))
        self.run_until_complete = resources.enter_context(
            pfunc('run_until_complete'))
        self.add_signal_handler = resources.enter_context(
            pfunc('add_signal_handler'))
        resources.enter_context(
            patch.object(logging.getLogger('mail.log'), 'info'))
        self.run_forever = resources.enter_context(pfunc('run_forever'))

    def test_loop(self):
        main(('-n',))
        # create_server() is called with a partial as the factory, and a
        # socket object.
        self.assertEqual(self.create_server.call_count, 1)
        positional, keywords = self.create_server.call_args
        self.assertEqual(positional[0].func, SMTP)
        self.assertEqual(len(positional[0].args), 1)
        self.assertIsInstance(positional[0].args[0], Debugging)
        self.assertEqual(positional[0].keywords, dict(
            data_size_limit=None,
            enable_SMTPUTF8=False))
        self.assertEqual(list(keywords), ['sock'])
        # run_until_complete() was called once.  The argument isn't important.
        self.assertTrue(self.run_until_complete.called)
        # add_signal_handler() is called with two arguments.
        self.assertEqual(self.add_signal_handler.call_count, 1)
        signal_number, callback = self.add_signal_handler.call_args[0]
        self.assertEqual(signal_number, signal.SIGINT)
        self.assertEqual(callback, self.loop.stop)
        # run_forever() was called once.
        self.assertEqual(self.run_forever.call_count, 1)

    def test_loop_keyboard_interrupt(self):
        # We mock out so much of this, is it even a worthwhile test?  Well, it
        # does give us coverage.
        self.run_forever.side_effect = KeyboardInterrupt
        main(('-n',))
        # loop.run_until_complete() was still executed.
        self.assertTrue(self.run_until_complete.called)

    def test_s(self):
        # We mock out so much of this, is it even a worthwhile test?  Well, it
        # does give us coverage.
        main(('-n', '-s', '3000'))
        positional, keywords = self.create_server.call_args
        self.assertEqual(positional[0].keywords, dict(
            data_size_limit=3000,
            enable_SMTPUTF8=False))

    def test_size(self):
        # We mock out so much of this, is it even a worthwhile test?  Well, it
        # does give us coverage.
        main(('-n', '--size', '3000'))
        positional, keywords = self.create_server.call_args
        self.assertEqual(positional[0].keywords, dict(
            data_size_limit=3000,
            enable_SMTPUTF8=False))

    def test_u(self):
        # We mock out so much of this, is it even a worthwhile test?  Well, it
        # does give us coverage.
        main(('-n', '-u'))
        positional, keywords = self.create_server.call_args
        self.assertEqual(positional[0].keywords, dict(
            data_size_limit=None,
            enable_SMTPUTF8=True))

    def test_smtputf8(self):
        # We mock out so much of this, is it even a worthwhile test?  Well, it
        # does give us coverage.
        main(('-n', '--smtputf8'))
        positional, keywords = self.create_server.call_args
        self.assertEqual(positional[0].keywords, dict(
            data_size_limit=None,
            enable_SMTPUTF8=True))


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


class TestSocket(unittest.TestCase):
    # Usually the socket will be set up from socket.getaddrinfo() but if that
    # raises socket.gaierror, then it tries to infer the IPv4/IPv6 type from
    # the host name.
    def setUp(self):
        self._resources = ExitStack()
        self.addCleanup(self._resources.close)
        self._resources.enter_context(patch('aiosmtpd.main.socket.getaddrinfo',
                                            side_effect=socket.gaierror))

    def test_ipv4(self):
        bind = self._resources.enter_context(patch('aiosmtpd.main.bind'))
        mock_sock = setup_sock('host.example.com', 8025)
        bind.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, 0)
        mock_sock.bind.assert_called_once_with(('host.example.com', 8025))

    def test_ipv6(self):
        bind = self._resources.enter_context(patch('aiosmtpd.main.bind'))
        mock_sock = setup_sock('::1', 8025)
        bind.assert_called_once_with(socket.AF_INET6, socket.SOCK_STREAM, 0)
        mock_sock.bind.assert_called_once_with(('::1', 8025, 0, 0))

    def test_bind_ipv4(self):
        self._resources.enter_context(patch('aiosmtpd.main.socket.socket'))
        mock_sock = setup_sock('host.example.com', 8025)
        mock_sock.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, True)

    def test_bind_ipv6(self):
        self._resources.enter_context(patch('aiosmtpd.main.socket.socket'))
        mock_sock = setup_sock('::1', 8025)
        self.assertEqual(mock_sock.setsockopt.call_args_list, [
            call(socket.SOL_SOCKET, socket.SO_REUSEADDR, True),
            call(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False),
            ])
