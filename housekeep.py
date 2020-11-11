import os
import sys
import pprint

from pathlib import Path


TOX_ENV_NAME = os.environ["TOX_ENV_NAME"]


def setup():
    os.makedirs("_dynamic", exist_ok=True)
    with open(f"_dynamic/ENV.{TOX_ENV_NAME}", "wt") as fout:
        pprint.pprint(dict(os.environ), stream=fout)


def cleanup():
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


if __name__ == '__main__':
    print(sys.version)
    print(sys.executable)
    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "cleanup":
        cleanup()
    else:
        raise RuntimeError(f"Unknown cmd {cmd}")
