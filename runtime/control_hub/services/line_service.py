"""Hardware-UART line-sensor service for the vendor's Raspberry Pi module."""

from __future__ import annotations

import os
import re
import subprocess
import time

from rg_runtime.line_sensor import LineFrameParser, LineSensorSnapshot


class LineSensorService:
    def __init__(self, *, transport: str = "hardware_uart", device: str = "/dev/ttyAMA0", rx_gpio: int = 15, tx_gpio: int = 14, baudrate: int = 115200, mode: str = "ascii_digital", active_level: int = 0, reverse_order: bool = False, enabled: bool = True, request_command: str = "$0,0,1#", startup_delay_s: float = 0.0, request_retry_s: float = 1.0, reader=None, serial_factory=None, pigpio_factory=None, clock=None, recover_leaked_claim: bool = True, pigpio_reset=None) -> None:
        if transport not in {"hardware_uart", "pigpio_soft_uart"}:
            raise ValueError("unsupported line transport")
        self.transport = transport
        self.device = device
        self.rx_gpio = rx_gpio
        self.tx_gpio = tx_gpio
        self.baudrate = baudrate
        self.enabled = enabled
        self.parser = LineFrameParser(mode=mode, active_level=active_level, reverse_order=reverse_order)
        self.request_command = request_command
        self.startup_delay_s = max(0.0, float(startup_delay_s))
        self.request_retry_s = max(0.1, float(request_retry_s))
        self.reader = reader
        self.serial_factory = serial_factory
        self.pigpio_factory = pigpio_factory
        self.clock = clock or time.monotonic
        self._serial = None
        self._pigpio = None
        self._rx_buffer = bytearray()
        self._next_request_at = 0.0
        self._warmup_until = 0.0
        self._streaming = False
        self._request_attempts = 0
        self._sensor_opened = False
        self.malformed_frames = 0
        self.recover_leaked_claim = bool(recover_leaked_claim) and os.environ.get("ROBOGAME_PIGPIO_AUTORESET", "1") != "0"
        self.pigpio_reset = pigpio_reset or self._restart_pigpiod
        self.snapshot = LineSensorSnapshot()

    @staticmethod
    def _restart_pigpiod() -> bool:
        """Release a stale GPIO claim.  Needs passwordless sudo; failure is safe."""
        try:
            completed = subprocess.run(
                ["sudo", "-n", "systemctl", "restart", "pigpiod"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            return False
        if completed.returncode != 0:
            return False
        time.sleep(2.0)
        return True

    def start(self) -> None:
        if not self.enabled:
            self.snapshot = LineSensorSnapshot(False, "DISABLED")
            return
        if self.reader is not None:
            self.snapshot = LineSensorSnapshot(True, "CONNECTED")
            return
        try:
            if self.transport == "pigpio_soft_uart":
                self._open_pigpio()
            else:
                self._open_hardware_uart()
            self._sensor_opened = True
            self._streaming = False
            self._request_attempts = 0
            self.malformed_frames = 0
            self._warmup_until = self.clock() + self.startup_delay_s
            self.snapshot = LineSensorSnapshot(True, "WAITING_DATA")
            # The vendor example sends this immediately. Retries cover a module
            # that is still in its documented 20-second power-on stabilization.
            self._request_stream()
            if self.startup_delay_s:
                self.snapshot.state = "WARMING_UP"
        except Exception as exc:
            self.close()
            self.snapshot = LineSensorSnapshot(False, "FAULT", str(exc))

    def poll_once(self) -> dict:
        if not self.snapshot.connected:
            return self.status()
        try:
            if (self._serial is not None or self._pigpio is not None) and not self._streaming and self.clock() >= self._next_request_at:
                self._request_stream()
            if not self._streaming and self.clock() < self._warmup_until:
                self.snapshot.state = "WARMING_UP"
            data = self.reader() if self.reader is not None else self._read_serial()
            if data:
                raw = bytes(data)
                self.snapshot.raw = self._format_raw(raw)
                now = int(time.monotonic() * 1000)
                states = self._parse_frames(raw, timestamp_ms=now)
                if states:
                    state = states[-1]
                    self._streaming = True
                    self.snapshot.line_state = state
                    self.snapshot.state = "LOST" if state.line_lost else "FOLLOWING"
                    self.snapshot.error = None
        except Exception as exc:
            self.snapshot.connected = False
            self.snapshot.state = "FAULT"
            self.snapshot.error = str(exc)
            self.snapshot.line_state = None
        return self.status()

    def _format_raw(self, raw: bytes) -> str:
        if self.parser.mode in {"ascii_digital", "ascii_bits", "ascii_hex"}:
            # Replace, not fail: one glitched byte must not turn the whole chunk
            # into an unreadable hex dump for the operator.
            return raw.decode("ascii", "replace").strip()
        return raw.hex(" ")

    def status(self) -> dict:
        state = self.snapshot.line_state
        return {
            "connected": self.snapshot.connected,
            "state": self.snapshot.state,
            "error": self.snapshot.error,
            "raw": self.snapshot.raw,
            "device": self.device,
            "transport": self.transport,
            "sensor_mask": state.sensor_mask if state else None,
            "sensors": [int((state.sensor_mask >> (7 - i)) & 1) for i in range(8)] if state else None,
            "line_error": state.line_error if state else None,
            "line_lost": state.line_lost if state else None,
            "intersection": state.intersection.value if state else None,
            "timestamp_ms": state.timestamp_ms if state else None,
            "rx_gpio": self.rx_gpio,
            "tx_gpio": self.tx_gpio,
            "baudrate": self.baudrate,
            "active_level": self.parser.active_level,
            "request_attempts": self._request_attempts,
            "malformed_frames": self.malformed_frames,
        }

    def close(self) -> None:
        # Only stop the module's stream if we are the ones who started it: on a
        # failed open the sensor may belong to another running program.
        if self._sensor_opened and (self._serial is not None or self._pigpio is not None):
            try:
                self._write_bytes(b"$0,0,0#")
            except Exception:
                pass
        self._sensor_opened = False
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
        if self._pigpio is not None:
            try:
                self._pigpio.bb_serial_read_close(self.rx_gpio)
            finally:
                self._pigpio.stop()
                self._pigpio = None
        self.snapshot.connected = False
        self.snapshot.state = "DISCONNECTED"
        self._streaming = False

    def _read_serial(self) -> bytes:
        if self._pigpio is not None:
            count, data = self._pigpio.bb_serial_read(self.rx_gpio)
            return bytes(data[:count]) if count else b""
        waiting = int(getattr(self._serial, "in_waiting", 0))
        return bytes(self._serial.read(waiting)) if waiting else b""

    def _request_stream(self) -> None:
        command = self.request_command.encode("ascii")
        if not command.startswith(b"$") or not command.endswith(b"#"):
            raise ValueError("request_command must be ASCII text framed by $ and #")
        self._write_bytes(command)
        self._request_attempts += 1
        self._next_request_at = self.clock() + self.request_retry_s
        self.snapshot.state = "WAITING_DATA"

    def _open_hardware_uart(self) -> None:
        if self.serial_factory is None:
            import serial
            factory = serial.Serial
            serial_options = {
                "bytesize": serial.EIGHTBITS,
                "parity": serial.PARITY_NONE,
                "stopbits": serial.STOPBITS_ONE,
            }
        else:
            factory = self.serial_factory
            serial_options = {"bytesize": 8, "parity": "N", "stopbits": 1}
        self._serial = factory(
            self.device,
            baudrate=self.baudrate,
            **serial_options,
            timeout=0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def _connect_pigpio(self):
        """Open a fresh pigpiod connection with the soft-UART pins configured."""
        if self.pigpio_factory is None:
            import pigpio
            gpio = pigpio.pi()
            input_mode, output_mode = pigpio.INPUT, pigpio.OUTPUT
        else:
            gpio = self.pigpio_factory()
            input_mode, output_mode = gpio.INPUT, gpio.OUTPUT
        if not gpio.connected:
            gpio.stop()
            raise RuntimeError("pigpio daemon is not reachable; start pigpiod")
        gpio.set_mode(self.rx_gpio, input_mode)
        gpio.set_mode(self.tx_gpio, output_mode)
        return gpio

    def _open_pigpio(self) -> None:
        gpio = self._connect_pigpio()
        # Assign before opening so a failed open still runs the full close().
        self._pigpio = gpio
        try:
            result = gpio.bb_serial_read_open(self.rx_gpio, self.baudrate, 8)
        except Exception as exc:
            if "in use" not in str(exc).lower():
                raise
            if not self._recover_claimed_gpio(exc):
                raise
            # Releasing a stale claim restarts pigpiod, which takes our own
            # connection down with it.  Retrying on the dead handle fails with
            # a broken pipe instead of opening the port, so reconnect first.
            try:
                gpio.stop()
            except Exception:
                pass
            gpio = self._connect_pigpio()
            self._pigpio = gpio
            result = gpio.bb_serial_read_open(self.rx_gpio, self.baudrate, 8)
        if result not in (None, 0):
            raise RuntimeError(f"software UART open on GPIO{self.rx_gpio} failed: {result}")

    def _pigpio_peer_count(self) -> int | None:
        """Other processes holding a pigpiod connection, or None if unknown.

        Counting `ss` output lines does not work.  One ``pigpio.pi()`` opens two
        sockets, and ``ss`` lists every loopback socket twice (once per end), so
        a single client produced four matching lines and the old
        ``len(peers) - 1`` always returned 3 -- even when this process was the
        only client on the machine.  Because the caller treats any count above
        zero as "another program is using the sensor", the stale-claim recovery
        below was unreachable: a claim leaked by a crashed run could never be
        released, and the operator was told to stop a program that did not
        exist.

        Attribute sockets to owning pids instead, and drop our own pid.  Only
        processes this user may inspect are visible, which is the right
        trade-off for a recovery path: under-counting at worst restarts pigpiod
        and disturbs a peer that is already wedged, while over-counting leaves
        the sensor permanently unusable.
        """
        try:
            completed = subprocess.run(
                ["ss", "-tnp", "state", "established"], capture_output=True, text=True, timeout=5
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        pids: set[int] = set()
        for line in completed.stdout.splitlines():
            if ":8888" not in line:
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.add(int(match.group(1)))
        pids.discard(os.getpid())
        return len(pids)

    def _recover_claimed_gpio(self, exc: Exception) -> bool:
        """`GPIO already in use` has two very different causes; tell them apart.

        A live client (the runtime hub, or a previous route run) holding the
        sensor must NOT be disturbed.  A claim left behind by a client that died
        without closing -- SIGKILL, or a SIGTERM the program never handled, which
        is what stranded the robot on 2026-09-14 -- lives on inside pigpiod with
        nobody attached, and only a daemon restart releases it.

        Returns True when the daemon was restarted, in which case the caller's
        own pigpiod connection is dead and must be rebuilt before it retries.
        """
        peers = self._pigpio_peer_count()
        if peers is None:
            raise RuntimeError(
                f"the line sensor GPIO{self.rx_gpio} is already claimed. If another program "
                "(runtime hub or route v2) is running, stop it; if nothing is running, release "
                "the stale claim with: sudo systemctl restart pigpiod"
            ) from exc
        if peers > 0:
            raise RuntimeError(
                f"the line sensor is already open in another program ({peers} live pigpio "
                "client(s)); stop the runtime hub or the other route run first"
            ) from exc
        if not self.recover_leaked_claim:
            raise RuntimeError(
                f"GPIO{self.rx_gpio} is claimed by a died-without-closing pigpio client; "
                "release it with: sudo systemctl restart pigpiod"
            ) from exc
        if self.pigpio_reset is None or not self.pigpio_reset():
            raise RuntimeError(
                f"GPIO{self.rx_gpio} is claimed by a stale pigpio client and the automatic "
                "release failed; run: sudo systemctl restart pigpiod"
            ) from exc
        return True

    def _write_bytes(self, data: bytes) -> None:
        if self._pigpio is None:
            self._serial.write(data)
            flush = getattr(self._serial, "flush", None)
            if flush is not None:
                flush()
            return
        self._pigpio.wave_clear()
        self._pigpio.wave_add_serial(self.tx_gpio, self.baudrate, data, bb_bits=8)
        wave_id = self._pigpio.wave_create()
        if wave_id < 0:
            raise RuntimeError(f"pigpio failed to create serial waveform: {wave_id}")
        try:
            self._pigpio.wave_send_once(wave_id)
            deadline = self.clock() + 0.2
            while self._pigpio.wave_tx_busy():
                if self.clock() >= deadline:
                    raise TimeoutError("pigpio software UART transmit timed out")
                time.sleep(0.001)
        finally:
            self._pigpio.wave_delete(wave_id)

    def _parse_frames(self, data: bytes, *, timestamp_ms: int):
        # A single glitched byte or a read that starts mid-frame must never be
        # reported as a device fault: on a continuous 115200 stream both happen
        # constantly, and the previous implementation raised, which latched the
        # service into FAULT and stopped reading altogether.  Malformed frames
        # are counted and skipped; only real transport errors fault the service.
        if self.parser.mode == "bitmask_byte":
            states = []
            for value in data:
                state = self._parse_candidate(bytes([value]), timestamp_ms=timestamp_ms)
                if state is not None:
                    states.append(state)
            return states
        self._rx_buffer.extend(data)
        separator = b"#" if self.parser.mode == "ascii_digital" else b"\n"
        states = []
        while separator in self._rx_buffer:
            raw, _, remaining = self._rx_buffer.partition(separator)
            self._rx_buffer = bytearray(remaining)
            if self.parser.mode == "ascii_digital":
                # The digital module frames with '#' only, so a read can begin
                # inside a frame; only the last '$D,' can be a real frame start.
                start = raw.rfind(b"$D,")
                if start < 0:
                    self.malformed_frames += 1
                    continue
                raw = raw[start:] + b"#"
            state = self._parse_candidate(raw, timestamp_ms=timestamp_ms)
            if state is not None:
                states.append(state)
        return states

    def _parse_candidate(self, frame: bytes, *, timestamp_ms: int):
        try:
            return self.parser.parse(frame, timestamp_ms=timestamp_ms)
        except (UnicodeDecodeError, ValueError):
            self.malformed_frames += 1
            return None
