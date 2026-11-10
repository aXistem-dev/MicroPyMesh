"""Type one line into MicroPythonOS's asyncio REPL, slowly enough that it keeps every
character (60 ms apart), and print what comes back. Needs pyserial (it ships with mpremote).

    python3 tools/repl_type.py PORT "from mpos import AppManager; ..." [WAIT_S]
    python3 tools/repl_type.py PORT --stop

--stop stops the TaskManager, which drops the device to the plain REPL that mpremote can
drive, and checks that it got there (retrying a few times); exit status 1 if it did not.
"""
import sys
import time

import serial

STOP = "import mpos; mpos.TaskManager.stop()"


def _read(s, secs):
    end = time.time() + secs
    out = b""
    while time.time() < end:
        out += s.read(4096)
    return out


def type_line(s, line, wait):
    s.write(b"\r")
    time.sleep(0.3)
    s.read(65536)
    for ch in line.encode():
        s.write(bytes([ch]))
        time.sleep(0.06)
    s.write(b"\r")
    return _read(s, wait)


def plain_repl(s):
    """True when Ctrl-A enters the raw REPL (only the plain REPL does that); leaves it."""
    s.write(b"\x03")
    time.sleep(0.3)
    s.read(65536)
    s.write(b"\x01")
    got = _read(s, 1.0)
    s.write(b"\x02")
    _read(s, 0.5)
    return b"raw REPL" in got


def main():
    port, line = sys.argv[1], sys.argv[2]
    s = serial.Serial(port, 115200, timeout=0.1)
    if line == "--stop":
        for _ in range(4):
            if plain_repl(s):
                print("plain REPL")
                return 0
            type_line(s, STOP, 3)
        print("could not reach the plain REPL")
        return 1
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    sys.stdout.write(type_line(s, line, wait).decode("utf-8", "replace") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
