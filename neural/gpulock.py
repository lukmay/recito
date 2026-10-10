"""
GPU lock for test runs: several large models do not fit into GPU memory at
the same time. Every process that loads a model onto the GPU runs through it:

    python neural/gpulock.py <command> [args...]

Blocks until the lock is free, runs the command, then releases the lock.
Exit code = that of the command. For development and benchmarks only; the
daemon does not use it.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from server_base import data_dir, leave_store_python  # noqa: E402

LOCK = data_dir() / "gpu.lock"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+")
    t0 = time.time()
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(2)
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX)
    waited = time.time() - t0
    if waited > 1:
        print(f"[gpulock] waited {waited:.0f} s", flush=True)
    try:
        return subprocess.call(sys.argv[1:])
    finally:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        fh.close()


if __name__ == "__main__":
    leave_store_python()
    sys.exit(main())
