import json

from control_hub.api import HubApplication
from control_hub.safety import HubSafety
from control_hub.services.event_log import EventLog
from control_hub.state import ControlLease, HubState
from rg_runtime.line_sensor import line_state_from_mask
from control_hub.services.line_service import LineSensorService


class FakeSerial:
    def __init__(self, *_args, **kwargs):
        self.kwargs = kwargs
        self.incoming = bytearray()
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, count):
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data

    def close(self):
        self.closed = True


class FakePigpio:
    INPUT = 0
    OUTPUT = 1

    def __init__(self):
        self.connected = True
        self.calls = []
        self.incoming = bytearray()

    def set_mode(self, gpio, mode): self.calls.append(("set_mode", gpio, mode))
    def bb_serial_read_open(self, gpio, baudrate, bits): self.calls.append(("read_open", gpio, baudrate, bits)); return 0
    def bb_serial_read(self, gpio):
        data = bytes(self.incoming)
        self.incoming.clear()
        return len(data), data
    def bb_serial_read_close(self, gpio): self.calls.append(("read_close", gpio)); return 0
    def wave_clear(self): self.calls.append(("wave_clear",)); return 0
    def wave_add_serial(self, gpio, baudrate, data, **kwargs): self.calls.append(("wave_add_serial", gpio, baudrate, bytes(data), kwargs)); return len(data)
    def wave_create(self): self.calls.append(("wave_create",)); return 1
    def wave_send_once(self, wave_id): self.calls.append(("wave_send_once", wave_id)); return 0
    def wave_tx_busy(self): return False
    def wave_delete(self, wave_id): self.calls.append(("wave_delete", wave_id)); return 0
    def stop(self): self.calls.append(("stop",))


class FakeDevice:
    def status(self):
        return {"connected": True, "state": "CONNECTED"}


class FakeLine:
    def status(self):
        return {"connected": True, "state": "FOLLOWING", "sensor_mask": 6, "line_error": -0.5, "line_lost": False, "intersection": "none"}


class FakeArm:
    def status(self): return {"connected": False}
    def stop(self): return self.status()


class FakeCamera:
    def status(self): return {"running": False}


def body(response):
    return json.loads(response.body.decode("utf-8"))


def test_line_status_is_readable_without_control_lease(tmp_path):
    state = HubState()
    lease = ControlLease(2000)
    log = EventLog()
    app = HubApplication(state, lease, HubSafety(FakeArm(), lease, log), FakeArm(), FakeCamera(), log, line_service=FakeLine(), static_root=tmp_path, clock_ms=lambda: 1)
    result = app.handle("GET", "/api/line/status")
    assert result.status == 200
    assert body(result)["line_error"] == -0.5


def test_line_service_keeps_documented_ascii_frame_for_operator_debugging():
    frame = b"$D,x1:0,x2:1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#"
    service = LineSensorService(reader=lambda: frame)
    service.start()
    service.poll_once()
    assert service.status()["raw"] == frame.decode("ascii")
    assert service.status()["active_level"] == 0


def test_glitched_byte_does_not_latch_a_permanent_fault():
    frames = iter([
        b"$D,x1:0,x2:1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#",
        b"$D,x1:0,x2:\xb1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#",
        b"$D,x1:0,x2:0,x3:1,x4:0,x5:0,x6:0,x7:0,x8:0#",
    ])
    service = LineSensorService(reader=lambda: next(frames))
    service.start()
    service.poll_once()
    assert service.status()["line_error"] is not None
    service.poll_once()
    status = service.status()
    assert status["state"] == "FOLLOWING"
    assert status["connected"] is True
    assert status["malformed_frames"] == 1
    # The last good state survives the glitch and reading continues.
    assert status["sensor_mask"] == 0b01100011
    service.poll_once()
    assert service.status()["sensor_mask"] == 0b00100000


def test_partial_leading_frame_is_discarded_not_faulted():
    # A read that starts inside a frame is normal on a continuous ASCII stream.
    service = LineSensorService(reader=lambda: b",x7:1,x8:1#$D,x1:0,x2:0,x3:1,x4:0,x5:0,x6:0,x7:0,x8:0#")
    service.start()
    status = service.poll_once()
    assert status["state"] == "FOLLOWING"
    assert status["connected"] is True
    assert status["sensor_mask"] == 0b00100000
    assert status["malformed_frames"] == 1


def test_line_service_uses_documented_hardware_uart_and_baudrate():
    created = []
    def factory(*args, **kwargs):
        serial = FakeSerial(*args, **kwargs)
        created.append(serial)
        return serial

    service = LineSensorService(serial_factory=factory)
    service.start()
    serial = created[0]

    assert service.status()["device"] == "/dev/ttyAMA0"
    assert service.status()["baudrate"] == 115200
    assert serial.kwargs["baudrate"] == 115200
    assert (serial.kwargs["bytesize"], serial.kwargs["parity"], serial.kwargs["stopbits"]) == (8, "N", 1)
    assert not serial.kwargs["rtscts"] and not serial.kwargs["xonxoff"]
    assert serial.writes == [b"$0,0,1#"]

    serial.incoming.extend(b"$D,x1:0,x2:1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#")
    status = service.poll_once()
    assert status["state"] == "FOLLOWING"
    service.close()
    assert serial.closed
    assert serial.writes[-1] == b"$0,0,0#"


def test_line_service_sends_vendor_request_immediately_and_retries_until_frame():
    created = []
    now = [0.0]

    def factory(*args, **kwargs):
        serial = FakeSerial(*args, **kwargs)
        created.append(serial)
        return serial

    service = LineSensorService(
        serial_factory=factory,
        startup_delay_s=20.0,
        request_retry_s=1.0,
        clock=lambda: now[0],
    )
    service.start()
    serial = created[0]
    assert serial.writes == [b"$0,0,1#"]

    now[0] = 0.9
    service.poll_once()
    assert serial.writes == [b"$0,0,1#"]

    now[0] = 1.0
    service.poll_once()
    assert serial.writes == [b"$0,0,1#", b"$0,0,1#"]


class ClaimedPigpio(FakePigpio):
    """pigpio whose software UART is already claimed, as after a leaked open."""

    def __init__(self, fail_times=1):
        super().__init__()
        self.fail_times = fail_times
        self.open_calls = 0

    def bb_serial_read_open(self, gpio, baudrate, bits):
        self.open_calls += 1
        self.calls.append(("read_open", gpio, baudrate, bits))
        if self.open_calls <= self.fail_times:
            raise RuntimeError("GPIO already in use")
        return 0


def _claimed_service(gpio, **kwargs):
    service = LineSensorService(
        transport="pigpio_soft_uart",
        rx_gpio=24,
        tx_gpio=23,
        baudrate=115200,
        startup_delay_s=0,
        pigpio_factory=lambda: gpio,
        **kwargs,
    )
    return service


def test_claimed_gpio_with_a_live_client_names_the_other_program():
    gpio = ClaimedPigpio()
    service = _claimed_service(gpio)
    service._pigpio_peer_count = lambda: 1
    service.start()
    assert service.snapshot.connected is False
    assert "another program" in service.snapshot.error
    # A failed open must not send the module's stop-stream command.
    assert ("wave_add_serial", 23, 115200, b"$0,0,0#") not in gpio.calls
    service.close()


def test_leaked_gpio_with_no_live_client_is_released_and_retried():
    gpio = ClaimedPigpio(fail_times=1)
    resets = []
    service = _claimed_service(gpio, pigpio_reset=lambda: (resets.append(1), True)[1])
    service._pigpio_peer_count = lambda: 0
    service.start()
    assert resets == [1]
    assert gpio.open_calls == 2
    assert service.snapshot.connected is True
    service.close()


def test_leaked_gpio_without_auto_release_reports_the_manual_remedy():
    gpio = ClaimedPigpio(fail_times=1)
    service = _claimed_service(gpio, recover_leaked_claim=False)
    service._pigpio_peer_count = lambda: 0
    service.start()
    assert service.snapshot.connected is False
    assert "sudo systemctl restart pigpiod" in service.snapshot.error
    service.close()


def test_line_service_uses_gpio23_24_pigpio_software_uart():
    gpio = FakePigpio()
    service = LineSensorService(
        transport="pigpio_soft_uart",
        rx_gpio=23,
        tx_gpio=24,
        baudrate=115200,
        startup_delay_s=0,
        pigpio_factory=lambda: gpio,
    )

    service.start()
    assert ("read_open", 23, 115200, 8) in gpio.calls
    assert any(call[:4] == ("wave_add_serial", 24, 115200, b"$0,0,1#") for call in gpio.calls)

    gpio.incoming.extend(b"$D,x1:0,x2:1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#")
    status = service.poll_once()
    assert status["state"] == "FOLLOWING"
    assert status["transport"] == "pigpio_soft_uart"

    service.close()
    assert ("read_close", 23) in gpio.calls
    assert ("stop",) in gpio.calls
