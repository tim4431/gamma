"""Folder-label path and tag helpers, shared by the agent tools and the
importers. Keep the rules in sync with frontend/src/libraryUtils.js
(parseFolderTags / cleanFolderSegment / cleanFolderPath)."""

import re


def parse_tags(raw: str) -> list[str]:
    """A properties.folder / properties.category value → list of tags."""
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def clean_segment(name: str) -> str:
    """"," is the tag separator and "/" the path separator, so neither may
    survive inside a path segment."""
    return re.sub(r"\s+", " ", re.sub(r"[,/]", " ", name or "")).strip()


def clean_path(path: str) -> str:
    return "/".join(s for s in (clean_segment(p) for p in (path or "").split("/")) if s)
