import os
import sys
import pprint

from pathlib import Path


TOX_ENV_NAME = os.environ.get("TOX_ENV_NAME", None)


def setup():
    os.makedirs("_dynamic", exist_ok=True)
    with open(f"_dynamic/ENV.{TOX_ENV_NAME}", "wt") as fout:
        pprint.pprint(dict(os.environ), stream=fout)


def cleanup():
    # Move profiling files to per-testenv dirs
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
    # Cleanup __pycache__ dirs (if any)
    aiosmtpdpath = Path("aiosmtpd")
    for f in aiosmtpdpath.rglob("*.py[co]"):
        f.unlink()
    for d in aiosmtpdpath.rglob("__pycache__"):
        d.rmdir()


def deldir(targ: Path):
    if not targ.exists():
        return
    items = sorted(targ.rglob("*"))
    items.reverse()
    for pp in items:
        if not pp.is_dir():
            pp.chmod(0o600)
            pp.unlink()
        else:
            pp.chmod(0o700)
            pp.rmdir()
    targ.rmdir()


def superclean():
    if TOX_ENV_NAME is not None:
        raise RuntimeError("Do NOT run this inside tox!")
    print("Running standard cleanup...")
    cleanup()
    print("Removing work dirs ... ", end="")
    for dd in (".tox", "_dynamic", "aiosmtpd.egg-info", "htmlcov", "build"):
        print(dd, end=" ")
        deldir(Path(dd))
    print("\nRemoving work files...")
    for fn in (".coverage", "coverage.xml", "diffcov.html"):
        fp = Path(fn)
        if fp.exists():
            fp.unlink()
    for fp in Path(".").glob("coverage-*.xml"):
        fp.unlink()
    for fp in Path(".").glob("diffcov-*.html"):
        fp.unlink()


if __name__ == '__main__':
    print(sys.version)
    print(sys.executable)
    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "cleanup":
        cleanup()
    elif cmd == "superclean":
        superclean()
    else:
        raise RuntimeError(f"Unknown cmd {cmd}")
