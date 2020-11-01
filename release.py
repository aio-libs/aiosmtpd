#!/usr/bin/env python3

import os
import subprocess
import sys

import aiosmtpd.smtp as smtpd

version = smtpd.__version__

choice = input(f'Release aiosmtpd {version} - correct? [y/N]: ')
if choice.lower() not in ('y', 'yes'):
    sys.exit('Release aborted')
else:
    # We're probably already in the right place
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Let's use *this* python to build, please
    subprocess.run([sys.executable, "setup.py", "sdist"])
    # Assuming twine is installed. And that we're only building .tar.gz
    subprocess.run(["twine", "check", f"dist/aiosmtpd-{version}.tar.gz"])
    # You should have an aiosmtpd bit setup in your ~/.pypirc - for twine
    subprocess.run(["twine", "upload", "--config-file", "~/.pypirc", "-r", "aiosmtpd", "dist/aiosmptd-{version}.tar.gz"])
    # Only tag when we've actually built and uploaded. If something goes wrong
    # we may need the tag somewhere else!
    # The annotation information should come from the changelog
    subprocess.run(["git", "tag", "-a", version])
    # And now push the tag, of course.
    subprocess.run(["git", "push", "upstream", "--tags"])
