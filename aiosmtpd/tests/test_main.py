__all__ = [
    'TestMain',
    ]


import logging
import unittest

from aiosmtpd.main import main, parseargs
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

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
