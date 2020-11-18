#!/usr/bin/env python3

import os
import sys
import pprint
import argparse

from pathlib import Path


FG_CYAN = "\x1b[1;96m"
BOLD = "\x1b[1m"
NORM = "\x1b[0m"

TOX_ENV_NAME = os.environ.get("TOX_ENV_NAME", None)


# region #### Helper funcs ############################################################


def deldir(targ: Path):
    if not targ.exists():
        return
    for pp in reversed(sorted(targ.rglob("*"))):
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
    targ.rmdir()


# endregion


# region #### Functional blocks #######################################################


def dump_env():
    os.makedirs("_dynamic", exist_ok=True)
    with open(f"_dynamic/ENV.{TOX_ENV_NAME}", "wt") as fout:
        pprint.pprint(dict(os.environ), stream=fout)


def moveprof():
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
    for f in aiosmtpdpath.rglob("*.py[co]"):
        if verbose:
            print(".", end="", flush=True)
        f.unlink()
    for d in aiosmtpdpath.rglob("__pycache__"):
        if verbose:
            print(".", end="", flush=True)
        d.rmdir()
    if verbose:
        print()


def superclean():
    """Remove work dirs & files. They are .gitignore'd anyways."""
    print(f"{BOLD}Removing work dirs ... {NORM}", end="")
    for dd in (".pytest-cache", ".tox", "_dynamic", "aiosmtpd.egg-info", "build",
               "htmlcov", "prof"):
        print(dd, end=" ", flush=True)
        deldir(Path(dd))
    print(f"\n{BOLD}Removing work files ...{NORM}", end="")
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
    moveprof()
    pycache_clean()


def dispatch_superclean():
    if TOX_ENV_NAME is not None:
        raise RuntimeError("Do NOT run this inside tox!")
    print(f"{BOLD}Running pycache cleanup ...{NORM}", end="")
    pycache_clean(verbose=True)
    superclean()


# endregion


def get_opts(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cmd", metavar="CMD", choices=["setup", "cleanup", "superclean"]
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(f"{FG_CYAN}Python interpreter details:")
    print(sys.version)
    print(sys.executable)
    print(NORM)
    opts = get_opts(sys.argv[1:])
    dispatcher = globals().get(f"dispatch_{opts.cmd}")
    dispatcher()
