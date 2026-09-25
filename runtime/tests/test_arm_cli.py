from rg_runtime.arm_cli import build_parser, main
from rg_runtime.transports import MemoryTransport


class ProbeTransport(MemoryTransport):
    RESPONSES = {
        "ARM,STOP\r\n": "ACK,STOPPED_LOCKED\r\n",
        "ARM,PING\r\n": "ACK,PONG\r\n",
        "ARM,STATUS\r\n": "STATE,LOCKED,CAL=1,SUCTION=0,ROUTINE=255,STEP=0,RX3=0\r\n",
    }

    def send_line(self, line):
        super().send_line(line)
        if not self._incoming and line in self.RESPONSES:
            self.feed(self.RESPONSES[line])


def test_parser_supports_list_probe_console_and_stop():
    parser = build_parser()
    assert parser.parse_args(["list"]).command == "list"
    assert parser.parse_args(["probe"]).command == "probe"
    assert parser.parse_args(["console"]).command == "console"
    assert parser.parse_args(["stop"]).command == "stop"


def test_probe_uses_only_safe_commands_and_stops_on_exit(tmp_path):
    transport = ProbeTransport()
    output = []
    result = main(
        ["--device", "memory", "--log", str(tmp_path / "probe.jsonl"), "probe"],
        transport_factory=lambda _device, _baudrate: transport,
        output_fn=output.append,
    )
    assert result == 0
    assert transport.sent == [
        "ARM,STOP\r\n",
        "ARM,PING\r\n",
        "ARM,STATUS\r\n",
        "ARM,STOP\r\n",
    ]
    assert any("LOCKED" in line for line in output)


def test_console_quit_does_not_enable_or_move(tmp_path):
    transport = ProbeTransport()
    result = main(
        ["--device", "memory", "--log", str(tmp_path / "console.jsonl"), "console"],
        transport_factory=lambda _device, _baudrate: transport,
        input_fn=lambda _prompt: "quit",
        output_fn=lambda _line: None,
    )
    assert result == 0
    assert not any("ENABLE" in line or "RUN" in line for line in transport.sent)
