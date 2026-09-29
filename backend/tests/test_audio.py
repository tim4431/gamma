"""Recording segments travel as normal assets; timings use recorder offsets."""

import pytest
from conftest import make_page
from gamma import audio, ink
from gamma.storage import upload_refs


def test_audio_upload_and_metadata_use_regular_assets_and_ops(guest):
    upload = guest.post("/api/upload-file", files={"file": ("segment.m4a", b"audio-test-segment", "audio/mp4")})
    assert upload.status_code == 200, upload.text
    url = upload.json()["url"]
    page = make_page(guest)["id"]
    props = {"type": "audio", "audio_segments": [{"id": "seg1", "url": url, "duration_ms": 2000}],
             "audio_events": [{"kind": "stroke", "segment_id": "seg1", "start_ms": 400, "end_ms": 600,
                               "block_id": "inkGroup", "stroke_id": "original", "pdf_page": 1}]}
    group = guest.post("/api/blocks", json={"parent_id": page, "content": "Lecture", "properties": props})
    assert group.status_code == 200, group.text
    assert url.rsplit("/", 1)[1] in upload_refs("", props)
    stream = guest.get(url, headers={"Range": "bytes=0-4"})
    assert stream.status_code == 206 and stream.content == b"audio"
    assert stream.headers["content-type"].startswith("audio/mp4")
    fragment = ink.Stroke(id="piece", source_id="original", ch="xyt", pts=[100, 100, 80])
    assert audio.stroke_event(props, "inkGroup", fragment)["start_ms"] == 400
    duplicate = fragment.model_copy(update={"id": "duplicate", "source_id": None})
    assert audio.stroke_event(props, "inkGroup", duplicate) is None


@pytest.mark.parametrize("events", [
    [{"kind": "page", "segment_id": "missing", "start_ms": 0, "end_ms": 0, "pdf_page": 1}],
    [{"kind": "page", "segment_id": "s", "start_ms": 2000, "end_ms": 3000, "pdf_page": 1}],
    [{"kind": "stroke", "segment_id": "s", "start_ms": 10, "end_ms": 20}],
])
def test_audio_invalid_segment_mapping_is_rejected(events):
    with pytest.raises(ValueError):
        audio.validate_properties({"type": "audio", "audio_segments": [{"id": "s", "url": "/api/uploads/aa.m4a", "duration_ms": 1000}],
                                   "audio_events": events})
