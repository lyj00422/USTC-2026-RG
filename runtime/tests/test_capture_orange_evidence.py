from types import SimpleNamespace, MappingProxyType

from tools.capture_orange_evidence import detection_record
from rg_runtime.blocks import RejectedBlockCandidate
from rg_runtime.models import BlockColor, BlockObservation


def test_detection_record_serializes_accepted_and_rejected_candidates():
    accepted = BlockObservation(
        color=BlockColor.ORANGE,
        bounding_box=(10, 20, 30, 40),
        center_px=(25.0, 40.0),
        area=1200.0,
        confidence=0.91,
        frame_index=7,
        timestamp_ns=123,
        color_channels=("hsv",),
    )
    rejected = RejectedBlockCandidate(
        reason="area_below_min",
        bounding_box=(1, 2, 3, 4),
        metrics=MappingProxyType({"area_px": 12.0}),
        frame_index=7,
        timestamp_ns=123,
    )
    result = SimpleNamespace(
        frame_index=7,
        timestamp_ns=123,
        accepted=(accepted,),
        rejected=(rejected,),
    )

    record = detection_record(result)

    assert record["frame_index"] == 7
    assert record["accepted"][0]["color"] == "orange"
    assert record["accepted"][0]["bounding_box"] == [10, 20, 30, 40]
    assert record["rejected"][0]["reason"] == "area_below_min"
    assert record["rejected"][0]["metrics"]["area_px"] == 12.0
