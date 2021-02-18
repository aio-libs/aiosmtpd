#!/usr/bin/env python3

# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from aiosmtpd import __version__ as version

TWINE_CONFIG = Path(os.environ.get("TWINE_CONFIG", "~/.pypirc")).expanduser()
TWINE_REPO = os.environ.get("TWINE_REPOSITORY", "aiosmtpd")
UPSTREAM_REMOTE = os.environ.get("UPSTREAM_REMOTE", "upstream")
GPG_SIGNING_ID = os.environ.get("GPG_SIGNING_ID")
DISTFILES = [
    f"dist/aiosmtpd-{version}.tar.gz",
    f"dist/aiosmtpd-{version}-py3-none-any.whl",
]

try:
    subprocess.run(["twine", "--version"], stdout=subprocess.PIPE)
except FileNotFoundError:
    print("Please install 'twine' first")
    sys.exit(1)
result = subprocess.run(["pip", "freeze"], stdout=subprocess.PIPE)
if b"\ntwine-verify-upload==" not in result.stdout:
    print("*** Package twine-verify-upload is not yet installed.")
    print("*** Consider installing it. It is very useful :)")
    has_verify = False
else:
    has_verify = True

print(
    f"""
TWINE_CONFIG = {TWINE_CONFIG}
TWINE_REPO = {TWINE_REPO}
UPSTREAM_REMOTE = {UPSTREAM_REMOTE}
GPG_SIGNING_ID = {GPG_SIGNING_ID or 'None'}
"""
)
choice = input(f"Release aiosmtpd {version} - correct? [y/N]: ")
if choice.lower() not in ("y", "yes"):
    sys.exit("Release aborted")

newsfile = Path(".") / "aiosmtpd" / "docs" / "NEWS.rst"
with newsfile.open("rt") as fin:
    want = re.compile("^" + re.escape(version) + r"\s*\(\d{4}-\d\d-\d\d\)")
    for ln in fin:
        m = want.match(ln)
        if not m:
            continue
        break
    else:
        print(f"ERROR: I found no datestamped entry for {version} in NEWS.rst!")
        sys.exit(1)

if not GPG_SIGNING_ID:
    choice = input("You did not specify GPG signing ID! Continue? [y/N]: ")
    if choice.lower() not in ("y", "yes"):
        sys.exit("Release aborted")

choice = input("Run tox first? [y/N]: ")
if choice.lower() in ("y", "yes"):
    subprocess.run("tox")

# We're probably already in the right place
os.chdir(Path(__file__).absolute().parent)

try:
    # Let's use *this* python to build, please
    subprocess.run([sys.executable, "setup.py", "sdist"], check=True)
    subprocess.run([sys.executable, "setup.py", "bdist_wheel"], check=True)
    for f in DISTFILES:
        assert Path(f).exists(), f"{f} is missing!"

    # Assuming twine is installed.
    subprocess.run(["twine", "check"] + DISTFILES, check=True)

    # You should have an aiosmtpd bit setup in your ~/.pypirc - for twine
    twine_up = f"twine upload --config-file {TWINE_CONFIG} -r {TWINE_REPO}".split()
    if GPG_SIGNING_ID:
        twine_up.extend(["--sign", "--identity", GPG_SIGNING_ID])
    twine_up.extend(DISTFILES)
    subprocess.run(twine_up, check=True)

    if has_verify:
        print("Waiting for package to be received by PyPI...", end="")
        for i in range(10, 0, -1):
            print(i, end="..")
            time.sleep(1.0)
        print()
        twine_verif = ["twine", "verify_upload"] + DISTFILES
        subprocess.run(twine_verif, check=True)
except subprocess.CalledProcessError as e:
    print("ERROR: Last step returned exitcode != 0")
    sys.exit(e.returncode)

# Only tag when we've actually built and uploaded. If something goes wrong
# we may need the tag somewhere else!
choice = input("tag and push? [Y/n]: ")
if choice.lower() not in ("n", "no"):
    pass
else:
    # The annotation information should come from the changelog
    subprocess.run(["git", "tag", "-a", version])
    # And now push the tag, of course.
    subprocess.run(["git", "push", UPSTREAM_REMOTE, "--tags"])
