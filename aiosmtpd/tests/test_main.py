__all__ = [
    'TestMain',
    ]


import logging
import unittest

from aiosmtpd.main import main
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

try:
    import pwd
except ImportError:
    pwd = None


log = logging.getLogger('mail.log')


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
