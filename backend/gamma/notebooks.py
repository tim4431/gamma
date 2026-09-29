"""Notebook paper is ordinary block data; PDFs are a derived export.

Sheets have stable block IDs and fractional order. Their point coordinates
never depend on the ordinal a sheet happens to occupy in an exported PDF.
"""

import io
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from PyPDF2 import PdfWriter
from PyPDF2.generic import DecodedStreamObject, NameObject

from .ink import Space


class Paper(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    width: float = Field(default=595.28, ge=24, le=10000)
    height: float = Field(default=841.89, ge=24, le=10000)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    pattern: Literal["blank", "ruled", "grid", "dots"] = "blank"
    spacing: float = Field(default=24, ge=4, le=1000)
    line_color: str = Field(default="#d6dce5", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def _pattern_budget(self):
        if self.pattern == "dots" and math.ceil(self.width / self.spacing) * math.ceil(self.height / self.spacing) > 100000:
            raise ValueError("dot paper exceeds 100000 dots per sheet")
        return self


class Notebook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    default_paper: Paper = Field(default_factory=Paper)


def validate_properties(props: dict) -> None:
    if props.get("notebook") is not None:
        Notebook.model_validate(props["notebook"])
    if props.get("type") == "notebook-sheet":
        if not isinstance(props.get("paper"), dict):
            raise ValueError("a notebook sheet needs paper properties")
        Paper.model_validate(props["paper"])


def sheets_for(blocks: list[dict], root_id: str) -> list[dict]:
    """Only direct sheet children belong to the notebook's paper stack."""
    return sorted((b for b in blocks if b.get("parent_id") == root_id
                   and b.get("properties", {}).get("type") == "notebook-sheet"),
                  key=lambda b: (b.get("position", ""), b["id"]))


def _rgb(hex_color: str) -> str:
    return " ".join(f"{int(hex_color[i:i + 2], 16) / 255:.5f}" for i in (1, 3, 5))


def paper_pdf(sheets: list[dict]) -> bytes:
    """Render real vector PDF backgrounds; no stored whole-PDF revision."""
    if not sheets:
        raise ValueError("the notebook has no sheets")
    writer = PdfWriter()
    for sheet in sheets:
        paper = Paper.model_validate(sheet["properties"]["paper"])
        w, h, step = paper.width, paper.height, paper.spacing
        page = writer.add_blank_page(width=w, height=h)
        ops = ["q", f"{_rgb(paper.color)} rg 0 0 {w:.4f} {h:.4f} re f",
               f"{_rgb(paper.line_color)} RG {_rgb(paper.line_color)} rg 0.5 w"]
        if paper.pattern in ("ruled", "grid"):
            for n in range(1, math.ceil(h / step)):
                y = h - n * step
                ops.append(f"0 {y:.4f} m {w:.4f} {y:.4f} l S")
        if paper.pattern == "grid":
            for n in range(1, math.ceil(w / step)):
                x = n * step
                ops.append(f"{x:.4f} 0 m {x:.4f} {h:.4f} l S")
        if paper.pattern == "dots":
            for row in range(1, math.ceil(h / step)):
                for col in range(1, math.ceil(w / step)):
                    x, y, r, k = col * step, h - row * step, 0.65, 0.358985
                    ops.append(f"{x+r:.4f} {y:.4f} m {x+r:.4f} {y+k:.4f} {x+k:.4f} {y+r:.4f} {x:.4f} {y+r:.4f} c "
                               f"{x-k:.4f} {y+r:.4f} {x-r:.4f} {y+k:.4f} {x-r:.4f} {y:.4f} c "
                               f"{x-r:.4f} {y-k:.4f} {x-k:.4f} {y-r:.4f} {x:.4f} {y-r:.4f} c "
                               f"{x+k:.4f} {y-r:.4f} {x+r:.4f} {y-k:.4f} {x+r:.4f} {y:.4f} c f")
        ops.append("Q")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def project_ink(groups: list[dict], sheets: list[dict]) -> list[dict]:
    """Resolve stable sheet IDs for export without scaling saved geometry."""
    locations = {b["id"]: (i + 1, Paper.model_validate(b["properties"]["paper"]))
                 for i, b in enumerate(sheets)}
    out = []
    for group in groups:
        ink = group["ink"]
        target = locations.get(ink.space.sheet_id)
        if ink.space.kind != "notebook-page" or target is None:
            continue
        number, paper = target
        # The annotation writer normalizes by Space dimensions. Use the
        # destination frame while leaving absolute points and widths intact.
        projected = ink.model_copy(update={"space": Space(kind="pdf-page", page=number,
                                                          width=paper.width, height=paper.height)})
        out.append({**group, "ink": projected})
    return out
