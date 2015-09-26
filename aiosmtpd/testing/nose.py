"""nose2 test infrastructure."""

__all__ = [
    'NosePlugin',
    ]


import os
import re
import doctest
import aiosmtpd

from nose2.events import Plugin


DOT = '.'
FLAGS = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE | doctest.REPORT_NDIFF
TOPDIR = os.path.dirname(aiosmtpd.__file__)



class NosePlugin(Plugin):
    configSection = 'aiosmtpd'

    def __init__(self):
        super(NosePlugin, self).__init__()
        self.patterns = []
        self.stderr = False
        def set_stderr(ignore):
            self.stderr = True
        self.addArgument(self.patterns, 'P', 'pattern',
                         'Add a test matching pattern')

    def getTestCaseNames(self, event):
        if len(self.patterns) == 0:
            # No filter patterns, so everything should be tested.
            return
        # Does the pattern match the fully qualified class name?
        for pattern in self.patterns:
            full_class_name = '{}.{}'.format(
                event.testCase.__module__, event.testCase.__name__)
            if re.search(pattern, full_class_name):
                # Don't suppress this test class.
                return
        names = filter(event.isTestMethod, dir(event.testCase))
        for name in names:
            full_test_name = '{}.{}.{}'.format(
                event.testCase.__module__,
                event.testCase.__name__,
                name)
            for pattern in self.patterns:
                if re.search(pattern, full_test_name):
                    break
            else:
                event.excludedNames.append(name)

    def handleFile(self, event):
        path = event.path[len(TOPDIR)+1:]
        if len(self.patterns) > 0:
            for pattern in self.patterns:
                if re.search(pattern, path):
                    break
            else:
                # Skip this doctest.
                return
        base, ext = os.path.splitext(path)
        if ext != '.rst':
            return
        test = doctest.DocFileTest(
            path, package='aiosmtpd',
            optionflags=FLAGS,
            )
        # Suppress the extra "Doctest: ..." line.
        test.shortDescription = lambda: None
        event.extraTests.append(test)

    ## def startTest(self, event):
    ##     import sys; print('vvvvv', event.test, file=sys.stderr)

    ## def stopTest(self, event):
    ##     import sys; print('^^^^^', event.test, file=sys.stderr)
