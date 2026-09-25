from rg_runtime.transports import MemoryTransport, SerialTransport


def test_memory_transport_preserves_lines_and_records_tx():
    transport = MemoryTransport(["OK STOP\r\n", "DONE D\n"])
    transport.send_line("STOP\r\n")
    assert transport.sent == ["STOP\r\n"]
    assert transport.read_lines() == ["OK STOP", "DONE D"]
    assert transport.read_lines() == []


class FakeSerial:
    def __init__(self):
        self.buffer = bytearray()
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.buffer)

    def feed(self, data: bytes):
        self.buffer.extend(data)

    def read(self, size: int):
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def write(self, data: bytes):
        self.writes.append(data)

    def close(self):
        self.closed = True


def test_serial_transport_buffers_partial_lines():
    serial = FakeSerial()
    transport = SerialTransport.from_serial(serial)
    serial.feed(b"ACK,PO")
    assert transport.read_lines() == []
    serial.feed(b"NG\r\nSTATE,LOCKED\n")
    assert transport.read_lines() == ["ACK,PONG", "STATE,LOCKED"]
