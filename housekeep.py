#!/usr/bin/env python3

import os
import sys
import pprint
import argparse

from pathlib import Path

try:
    # noinspection PyPackageRequirements
    from colorama import (
        Fore,
        Style,
        init as colorama_init,
    )
except ImportError:
    colorama_init = None

    class Fore:
        CYAN = "\x1b[1;96m"

    class Style:
        BRIGHT = "\x1b[1m"
        RESET_ALL = "\x1b[0m"


TOX_ENV_NAME = os.environ.get("TOX_ENV_NAME", None)

WORKDIRS = (
    ".pytest-cache",
    ".pytest_cache",
    ".tox",
    "_dynamic",
    "aiosmtpd.egg-info",
    "build",
    "htmlcov",
    "prof",
)


# region #### Helper funcs ############################################################


def deldir(targ: Path):
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
        if (i & 1023) == 0:
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
    prof_files = [
        f
        for p in ("*.prof", "*.svg")
        for f in profpath.glob(p)
    ]
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
        if verbose and (i % 63) == 0:
            print(".", end="", flush=True)
        f.unlink()
    for d in aiosmtpdpath.rglob("__pycache__"):
        if verbose:
            print(".", end="", flush=True)
        d.rmdir()
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
    for fn in (".coverage", "coverage.xml", "diffcov.html"):
        print(".", end="", flush=True)
        fp = Path(fn)
        if fp.exists():
            fp.unlink()
    for fp in Path(".").glob("coverage-*.xml"):
        print(".", end="", flush=True)
        fp.unlink()
    for fp in Path(".").glob("diffcov-*.html"):
        print(".", end="", flush=True)
        fp.unlink()
    print()


# endregion


# region #### Dispatchers #############################################################


def dispatch_setup():
    dump_env()


def dispatch_cleanup():
    move_prof()


def dispatch_superclean():
    if TOX_ENV_NAME is not None:
        raise RuntimeError("Do NOT run this inside tox!")
    print(f"{Style.BRIGHT}Running pycache cleanup ...", end="")
    pycache_clean(verbose=True)
    rm_work()


# endregion


def get_opts(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cmd", metavar="CMD", choices=["setup", "cleanup", "superclean"]
    )
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
    if os.environ.get("CI") == "true":
        # All the housekeeping steps are pointless on Travis CI / GitHub Actions;
        # they build and tear down their VMs everytime anyways.
        sys.exit(0)
    opts = get_opts(sys.argv[1:])
    dispatcher = globals().get(f"dispatch_{opts.cmd}")
    dispatcher()
    print(Style.RESET_ALL, end="", flush=True)
