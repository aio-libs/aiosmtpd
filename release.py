#!/usr/bin/env python3

# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import sys
from pathlib import Path

import aiosmtpd.smtp as smtpd

TWINE_CONFIG = Path(os.environ.get("TWINE_CONFIG", "~/.pypirc")).expanduser()
TWINE_REPO = os.environ.get("TWINE_REPOSITORY", "aiosmtpd")
UPSTREAM_REMOTE = os.environ.get("UPSTREAM_REMOTE", "upstream")
GPG_SIGNING_ID = os.environ.get("GPG_SIGNING_ID")

version = smtpd.__version__

print(f"""
TWINE_CONFIG = {TWINE_CONFIG}
TWINE_REPO = {TWINE_REPO}
UPSTREAM_REMOTE = {UPSTREAM_REMOTE}
GPG_SIGNING_ID = {GPG_SIGNING_ID or 'None'}
""")
choice = input(f"Release aiosmtpd {version} - correct? [y/N]: ")
if choice.lower() not in ("y", "yes"):
    sys.exit("Release aborted")
else:
    # We're probably already in the right place
    os.chdir(Path(__file__).absolute().parent)
    # Let's use *this* python to build, please
    subprocess.run([sys.executable, "setup.py", "sdist"])
    # Assuming twine is installed. And that we're only building .tar.gz
    subprocess.run(["twine", "check", f"dist/aiosmtpd-{version}.tar.gz"])
    # You should have an aiosmtpd bit setup in your ~/.pypirc - for twine
    twine_up = [
        "twine", "upload", "--config-file", str(TWINE_CONFIG), "-r", TWINE_REPO
    ]
    if GPG_SIGNING_ID:
        twine_up.extend(["-s", "-i", GPG_SIGNING_ID])
    twine_up.append(f"dist/aiosmptd-{version}.tar.gz")
    subprocess.run(twine_up)
    # Only tag when we've actually built and uploaded. If something goes wrong
    # we may need the tag somewhere else!
    # The annotation information should come from the changelog
    subprocess.run(["git", "tag", "-a", version])
    # And now push the tag, of course.
    subprocess.run(["git", "push", UPSTREAM_REMOTE, "--tags"])
