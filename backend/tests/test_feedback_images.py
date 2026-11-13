"""The plan's gate for feedback on a work folder's pictures (step 3 of
docs/research/file-explorer.md, docs/dev/feedback.md): a work folder's PNG
is a page; a region drawn on it, a thread on the region and one on the
page reach ``.gamma/feedback/open.json`` with the picture's path and format,
the region's place in the picture's own pixels, and a JPEG of the crop and
of the whole picture in ``img/``; a rebuild of the picture leaves the
region where it was, drawn on an earlier version, with the held version's
crop beside the current one."""

import io
import json

from PIL import Image, ImageDraw

from conftest import thread_on
from gamma import storage
from gamma.blocks_store import new_block_id
from gamma.feedback import files as feedback_files, kept
from test_vault_sync import insert
from test_work_folder_rounds import owner, quick, work  # noqa: F401 — a work folder's vault and its quick rounds

FIGURE = (300, 200, 600, 400)   # a blue figure on the first version, top-left origin
BLUE = (0, 0, 200)


def _png(width, height, figure=None) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    if figure:
        ImageDraw.Draw(image).rectangle(figure, fill=BLUE)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _open(v) -> dict:
    return json.loads((v.root / feedback_files.DIR / "open.json").read_text("utf-8"))


def _jpeg(v, rel):
    image = Image.open(v.root / feedback_files.DIR / rel)
    assert image.format == "JPEG"
    return image.convert("RGB")


def test_comments_on_a_work_folders_picture_reach_its_feedback_files(work, owner):
    v = work("figures")
    data = _png(1200, 800, FIGURE)
    v.write("figures/fig.png", data, age=10)
    v.sync()
    page, ws = v.page_of("figures/fig.png"), v.ws
    doc = v.props(page)["doc"]
    assert (doc["format"], doc["hash"], doc["ext"], doc["at"]) == ("image", storage.content_digest(data), ".png", "path")
    region = new_block_id()
    rect = dict(zip(("x1", "y1", "x2", "y2"), FIGURE))
    v.edit(page, insert(page, "", {"image_position": {"width": 1200, "height": 800, "boundingRect": rect, "rects": [rect],
                                                      "hash": doc["hash"]}, "color": "#ff0000"}, region))
    on_region = thread_on(owner, page, region, "make the legend larger")
    on_page = thread_on(owner, page, page, "the colours are too light")
    assert kept.wait() and storage.exists(ws, f"{doc['hash']}.png")        # the version commented on is kept

    assert feedback_files.write(v.link) == {"open": 2}
    opened = _open(v)
    assert [e["id"] for e in opened["entries"]] == [on_page["id"], on_region["id"]]
    whole, part = opened["entries"]
    assert (whole["file"], whole["format"], whole["kind"], whole["where"]) == ("figures/fig.png", "image", "file", {})
    assert (part["file"], part["format"], part["kind"], part["block_id"]) == ("figures/fig.png", "image", "region", region)
    assert part["where"] == {"box": [0.245, 0.245, 0.505, 0.505], "rect": [300, 200, 600, 400], "size": [1200, 800],
                             "placed": "exact"}
    assert part["source"] is None and whole["before"] is None and part["before"] is None
    crop = _jpeg(v, part["picture"])
    assert crop.size == (1248, 832)                                           # 312 × 208 of the picture's pixels at 4×
    r, g, b = crop.getpixel((crop.width // 2, crop.height // 2))
    assert b > 120 and g < 40 and 40 < r < 100                                 # the figure through the region's red wash
    assert min(crop.getpixel((5, 5))) > 240
    picture = _jpeg(v, whole["picture"])
    assert max(picture.size) == 1568 and min(picture.getpixel((10, 10))) > 240
    r, g, b = picture.getpixel((round(450 / 1200 * picture.width), round(300 / 800 * picture.height)))
    assert b > 120 and r < 100
    text = (v.root / feedback_files.DIR / "open.md").read_text("utf-8")
    assert "## figures/fig.png\n\n### 1. The whole file\n" in text and "### 2. Region · 300×200 px at (300, 200)\n" in text

    # the picture rebuilt: the region stays where it was, drawn on an earlier version, its before the held version's crop
    other = _png(1200, 800)
    v.write("figures/fig.png", other, age=5)
    v.sync()
    assert v.props(page)["doc"]["hash"] == storage.content_digest(other)
    assert feedback_files.write(v.link) == {"open": 2}
    whole, part = _open(v)["entries"]
    assert part["where"]["placed"] == "earlier" and part["where"]["rect"] == [300, 200, 600, 400]
    now, before = _jpeg(v, part["picture"]), _jpeg(v, part["before"])
    assert min(now.getpixel((now.width // 2, now.height // 2))) > 150         # white under the wash on the new version
    assert before.getpixel((before.width // 2, before.height // 2))[2] > 120   # the figure of the version commented on
    assert whole["before"] and _jpeg(v, whole["before"]).getpixel((round(450 / 1200 * 1568), round(300 / 800 * 1045)))[2] > 120
    assert "drawn on an earlier version of the picture" in (v.root / feedback_files.DIR / "open.md").read_text("utf-8")
