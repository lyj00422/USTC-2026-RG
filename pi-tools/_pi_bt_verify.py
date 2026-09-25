"""One-shot chassis link check after a Bluetooth reconnect.

Sends STOP and SPD and prints whatever the firmware says back, so a link that
is merely "connected" at the RFCOMM layer but not actually carrying serial
traffic is caught before the operator tries to drive.
"""
import os
import select
import termios
import time

DEV = "/dev/robogame-chassis"

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
attrs = termios.tcgetattr(fd)
attrs[0] = 0
attrs[1] = 0
attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
attrs[3] = 0
attrs[4] = termios.B9600
attrs[5] = termios.B9600
termios.tcsetattr(fd, termios.TCSANOW, attrs)
termios.tcflush(fd, termios.TCIOFLUSH)


def ask(cmd, wait=1.5):
    os.write(fd, (cmd + "\n").encode())
    deadline = time.time() + wait
    buf = b""
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                buf += os.read(fd, 256)
            except BlockingIOError:
                pass
    return buf.decode("utf-8", "replace").strip()


print("STOP ->", repr(ask("STOP")))
time.sleep(0.3)
for _ in range(3):
    print("V 0 0 0 ->", repr(ask("V 0 0 0", wait=0.8)))
    time.sleep(0.2)
print("SPD ->", repr(ask("SPD", wait=1.0)))
print("STOP ->", repr(ask("STOP")))
os.close(fd)
