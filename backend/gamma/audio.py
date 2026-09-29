"""Portable recording metadata stored on ordinary Gamma blocks.

Audio offsets use a segment's recorder clock, never stroke wall-clock t0.
Files remain ordinary content-hashed uploads, covered by Mirror and backups.
"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID = r"^[A-Za-z0-9_-]+$"


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=_ID)
    url: str = Field(pattern=r"^/api/uploads/[0-9a-f]+\.(m4a|aac|mp3|wav|webm|ogg|mp4)$")
    duration_ms: int = Field(ge=0)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["page", "stroke"]
    segment_id: str = Field(min_length=1, max_length=64, pattern=_ID)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    block_id: str | None = Field(default=None, max_length=64, pattern=_ID)
    stroke_id: str | None = Field(default=None, max_length=32, pattern=_ID)
    sheet_id: str | None = Field(default=None, max_length=64, pattern=_ID)
    pdf_page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _event_shape(self):
        if self.end_ms < self.start_ms:
            raise ValueError("audio event ends before it starts")
        if self.kind == "stroke" and (not self.block_id or not self.stroke_id):
            raise ValueError("a stroke event needs block_id and stroke_id")
        if self.sheet_id is not None and self.pdf_page is not None:
            raise ValueError("an event cannot target both a sheet and a PDF page")
        if self.kind == "page" and self.sheet_id is None and self.pdf_page is None:
            raise ValueError("a page event needs a sheet_id or pdf_page")
        return self


def validate_properties(props: dict) -> None:
    if props.get("type") != "audio":
        return
    raw_segments, raw_events = props.get("audio_segments", []), props.get("audio_events", [])
    if not isinstance(raw_segments, list) or not isinstance(raw_events, list):
        raise ValueError("audio segments and events must be lists")
    if len(raw_segments) > 10000 or len(raw_events) > 100000:
        raise ValueError("recording metadata exceeds its budget")
    segments = [Segment.model_validate(s) for s in raw_segments]
    durations = {s.id: s.duration_ms for s in segments}
    if len(durations) != len(segments):
        raise ValueError("audio segment IDs must be unique")
    for raw in raw_events:
        event = Event.model_validate(raw)
        if event.segment_id not in durations:
            raise ValueError("audio event references an unknown segment")
        if event.end_ms > durations[event.segment_id]:
            raise ValueError("audio event extends past its segment")


def stroke_event(props: dict, block_id: str, stroke) -> dict | None:
    """Lineage lets a surviving erased fragment retain its original replay."""
    ids = {stroke.id, stroke.source_id} - {None}
    return next((event for event in props.get("audio_events", [])
                 if event.get("kind") == "stroke" and event.get("block_id") == block_id
                 and event.get("stroke_id") in ids), None)
