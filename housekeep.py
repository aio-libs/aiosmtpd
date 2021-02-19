#!/usr/bin/env python3

# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import argparse
import inspect
import os
import pprint
import sys
from pathlib import Path

try:
    # noinspection PyPackageRequirements
    from colorama import (  # pytype: disable=import-error
        Fore,
        Style,
        init as colorama_init,
    )
except ImportError:
    colorama_init = None

    class Fore:
        CYAN = "\x1b[1;96m"
        GREEN = "\x1b[1;92m"
        YELLOW = "\x1b[1;93m"

    class Style:
        BRIGHT = "\x1b[1m"
        RESET_ALL = "\x1b[0m"


TOX_ENV_NAME = os.environ.get("TOX_ENV_NAME", None)

WORKDIRS = (
    ".mypy_cache",
    ".pytype",
    ".pytest-cache",
    ".pytest_cache",
    ".tox",
    "_dynamic",
    "aiosmtpd.egg-info",
    "build",
    "dist",
    "htmlcov",
    "prof",
)

WORKFILES = (
    ".coverage",
    "coverage.xml",
    "diffcov.html",
    "coverage-*.xml",
    "diffcov-*.html",
)


# region #### Helper funcs ############################################################


def deldir(targ: Path, verbose: bool = True):
    if not targ.exists():
        return
    for i, pp in enumerate(reversed(sorted(targ.rglob("*"))), start=1):
        if pp.is_symlink():
            pp.unlink()
        elif pp.is_file():
            pp.chmod(0o600)
            pp.unlink()
        elif pp.is_dir():
            pp.chmod(0o700)
            pp.rmdir()
        else:
            raise RuntimeError(f"Don't know how to handle '{pp}'")
        if verbose and (i & 0x1FF) == 0:
            print(".", end="", flush=True)
    targ.rmdir()


# endregion


# region #### Functional blocks #######################################################


def dump_env():
    os.makedirs("_dynamic", exist_ok=True)
    with open(f"_dynamic/ENV.{TOX_ENV_NAME}", "wt") as fout:
        pprint.pprint(dict(os.environ), stream=fout)


def move_prof():
    """Move profiling files to per-testenv dirs"""
    profpath = Path("prof")
    # fmt: off
    prof_files = [
        filepath
        for fileglob in ("*.prof", "*.svg")
        for filepath in profpath.glob(fileglob)
    ]
    # fmt: on
    if not prof_files:
        return
    targpath = profpath / TOX_ENV_NAME
    os.makedirs(targpath, exist_ok=True)
    for f in targpath.glob("*"):
        f.unlink()
    for f in prof_files:
        f.rename(targpath / f.name)


def pycache_clean(verbose=False):
    """Cleanup __pycache__ dirs & bytecode files (if any)"""
    aiosmtpdpath = Path(".")
    for i, f in enumerate(aiosmtpdpath.rglob("*.py[co]"), start=1):
        if verbose and (i % 0x3FF) == 0:
            print(".", end="", flush=True)
        f.unlink()
    for d in aiosmtpdpath.rglob("__pycache__"):
        if verbose:
            print(".", end="", flush=True)
        deldir(d, verbose)
    if verbose:
        print()


def rm_work():
    """Remove work dirs & files. They are .gitignore'd anyways."""
    print(f"{Style.BRIGHT}Removing work dirs ... ", end="")
    # The reason we list WORKDIRS explicitly is because we don't want to accidentally
    # bork IDE workdirs such as .idea/ or .vscode/
    for dd in WORKDIRS:
        print(dd, end="", flush=True)
        deldir(Path(dd))
        print(" ", end="", flush=True)
    print(f"\n{Style.BRIGHT}Removing work files ...", end="")
    for fnglob in WORKFILES:
        for fp in Path(".").glob(fnglob):
            print(".", end="", flush=True)
            fp.exists() and fp.unlink()
    print()


# endregion


# region #### Dispatchers #############################################################


def dispatch_prep():
    """
    Prepare work directories and dump env vars
    """
    dump_env()


def dispatch_gather():
    """
    Gather inspection results into per-testenv dirs
    """
    move_prof()


def dispatch_remcache():
    """
    Remove all .py[co] files and all __pycache__ dirs
    """
    pycache_clean()


def dispatch_superclean():
    """
    Total cleaning of all test artifacts
    """
    if TOX_ENV_NAME is not None:
        raise RuntimeError("Do NOT run this inside tox!")
    print(f"{Style.BRIGHT}Running pycache cleanup ...", end="")
    pycache_clean(verbose=True)
    rm_work()


# endregion


def get_opts(argv):
    # From: https://stackoverflow.com/a/49999185/149900
    class NoAction(argparse.Action):
        def __init__(self, **kwargs):
            kwargs.setdefault("default", argparse.SUPPRESS)
            kwargs.setdefault("nargs", 0)
            super().__init__(**kwargs)

        def __call__(self, *args, **kwargs):
            pass

    dispers = {
        name.replace("dispatch_", ""): inspect.getdoc(obj)
        for name, obj in inspect.getmembers(sys.modules[__name__])
        if name.startswith("dispatch_") and inspect.isfunction(obj)
    }

    parser = argparse.ArgumentParser()
    parser.register("action", "no_action", NoAction)

    parser.add_argument(
        "--force", "-F", action="store_true", help="Force action even if in CI"
    )

    # From: https://stackoverflow.com/a/49999185/149900
    parser.add_argument(
        "cmd", metavar="COMMAND", choices=sorted(dispers.keys()), help="(See below)"
    )
    cgrp = parser.add_argument_group(title="COMMAND is one of")
    for name, doc in sorted(dispers.items()):
        cgrp.add_argument(name, help=doc, action="no_action")

    return parser.parse_args(argv)


def python_interp_details():
    print(f"{Fore.CYAN}Python interpreter details:")
    details = sys.version.splitlines() + sys.executable.splitlines()
    for ln in details:
        print(f"    {Fore.CYAN}{ln}")
    print(Style.RESET_ALL, end="", flush=True)


if __name__ == "__main__":
    colorama_init is None or colorama_init(autoreset=True)
    python_interp_details()
    opts = get_opts(sys.argv[1:])
    if os.environ.get("CI") == "true":
        if not opts.force:
            # All the housekeeping steps are pointless on Travis CI / GitHub Actions;
            # they build and tear down their VMs everytime anyways.
            print(
                f"{Fore.YELLOW}Skipping housekeeping because we're in CI and "
                f"--force not specified"
            )
            sys.exit(0)
        else:
            print(f"{Fore.YELLOW}We're in CI but --force is specified")
    print(f"{Fore.GREEN}{Path(__file__).name} {opts.cmd}{Style.RESET_ALL}")
    dispatcher = globals().get(f"dispatch_{opts.cmd}")
    dispatcher()
    # Defensive reset
    print(Style.RESET_ALL, end="", flush=True)
