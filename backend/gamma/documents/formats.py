"""What a file is, by its name: the format registry (docs/dev/documents.md).

A document's ``format`` is one of ``FORMATS``. ``EXTENSIONS`` is the one
table of the extensions Gamma knows: the format each one is, the language
of a text one, and how a stored file with it is served (its media type,
inline or as a download, sandboxed or not). ``NAMES`` knows the whole names
that carry no extension (``Makefile``, ``.gitignore``). A name neither knows
is ``binary``, unless its first bytes say otherwise (``sniff``). The serving
views (``IMAGE_MEDIA_TYPES`` …) are read off the table, so a row added there
is served right everywhere. Twin of frontend/src/documents/formats.js for
names; tests/shared/formats.json holds both to the same answers.
"""

import codecs
import re
from dataclasses import dataclass

FORMATS = ("pdf", "markdown", "text", "image", "audio", "video", "binary")

# A stored extension is ``.`` + 1-12 lowercase letters/digits; a name with
# none (or an odd one) is stored as ``.bin``.
EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,12}\Z")
# Files an OS runs as code when opened: the one kind Gamma refuses to host,
# on upload and (belt and braces) on serving. Archives and package formats
# (zip, whl, dmg, deb …) are fine: a lab shares software.
BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".msi", ".msix", ".appx",
    ".dll", ".cpl", ".sys", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta",
    ".ps1", ".psm1", ".reg", ".lnk", ".jar", ".app",
})


@dataclass(frozen=True)
class Ext:
    """One extension's row: its format, the language of a text one, and how
    a stored file with it is served. ``media`` None is
    ``application/octet-stream``; ``inline`` files are rendered by the
    browser on navigation, every other one gets ``Content-Disposition:
    attachment``; ``sandboxed`` ones get a sandboxing CSP too, in case a
    browser renders them anyway (an SVG or HTML file opened as a top-level
    document would run its scripts in this origin)."""
    format: str
    language: str | None = None
    media: str | None = None
    inline: bool = False
    sandboxed: bool = False


def _text(language: str, *exts: str) -> dict:
    return {e: Ext("text", language) for e in exts}


EXTENSIONS: dict[str, Ext] = {
    ".pdf": Ext("pdf", media="application/pdf", inline=True),
    ".md": Ext("markdown", media="text/markdown; charset=utf-8", inline=True),
    ".markdown": Ext("markdown"),
    ".txt": Ext("text", "plaintext", "text/plain; charset=utf-8", inline=True),
    ".log": Ext("text", "plaintext"),
    ".csv": Ext("text", "csv", "text/csv; charset=utf-8"),
    ".tsv": Ext("text", "tsv"),
    ".json": Ext("text", "json", "application/json"),
    ".jsonl": Ext("text", "json"),
    ".yaml": Ext("text", "yaml", "application/yaml"),
    ".yml": Ext("text", "yaml", "application/yaml"),
    ".toml": Ext("text", "toml", "application/toml"),
    ".xml": Ext("text", "xml", "application/xml"),
    **_text("ini", ".ini", ".cfg", ".conf"),
    ".tex": Ext("text", "latex", "application/x-tex"),
    **_text("latex", ".ltx", ".cls", ".sty"),
    ".bib": Ext("text", "bibtex", "application/x-bibtex"),
    ".py": Ext("text", "python", "text/x-python; charset=utf-8"),
    ".ipynb": Ext("text", "json", "application/x-ipynb+json"),
    ".nb": Ext("text", "mathematica", "application/mathematica"),
    ".m": Ext("text", "matlab", "text/plain; charset=utf-8"),
    ".html": Ext("text", "html", "text/html; charset=utf-8", sandboxed=True),
    **_text("javascript", ".js", ".mjs", ".cjs"),
    **_text("typescript", ".ts"),
    **_text("jsx", ".jsx"),
    **_text("tsx", ".tsx"),
    **_text("css", ".css"),
    **_text("scss", ".scss"),
    **_text("c", ".c", ".h"),
    **_text("cpp", ".cc", ".cpp", ".cxx", ".hpp", ".hh"),
    **_text("rust", ".rs"),
    **_text("go", ".go"),
    **_text("java", ".java"),
    **_text("kotlin", ".kt"),
    **_text("swift", ".swift"),
    **_text("ruby", ".rb"),
    **_text("php", ".php"),
    **_text("shell", ".sh", ".bash", ".zsh"),
    **_text("r", ".r"),
    **_text("julia", ".jl"),
    **_text("lua", ".lua"),
    **_text("sql", ".sql"),
    **_text("perl", ".pl"),
    **_text("haskell", ".hs"),
    **_text("scala", ".scala"),
    **_text("diff", ".diff", ".patch"),
    **_text("rst", ".rst"),
    **_text("typst", ".typ"),
    ".png": Ext("image", media="image/png", inline=True),
    ".jpg": Ext("image", media="image/jpeg", inline=True),
    ".jpeg": Ext("image", media="image/jpeg", inline=True),
    ".gif": Ext("image", media="image/gif", inline=True),
    ".webp": Ext("image", media="image/webp", inline=True),
    ".svg": Ext("image", media="image/svg+xml", sandboxed=True),
    # Audio an <audio> element plays straight from /api/uploads/ (a
    # recording's segments, an audio file chip), so it is served inline.
    ".mp3": Ext("audio", media="audio/mpeg", inline=True),
    ".wav": Ext("audio", media="audio/wav", inline=True),
    ".webm": Ext("audio", media="audio/webm", inline=True),  # what MediaRecorder gives (gamma/recording.py)
    ".m4a": Ext("audio", media="audio/mp4", inline=True),
    ".ogg": Ext("audio", media="audio/ogg", inline=True),
    ".opus": Ext("audio", media="audio/ogg", inline=True),
    ".mp4": Ext("video", media="video/mp4"),
    ".mov": Ext("video"),
    ".m4v": Ext("video"),
    ".bin": Ext("binary"),
    ".ink": Ext("binary", media="application/json"),  # handwriting groups (gamma/ink.py)
    ".docx": Ext("binary", media="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": Ext("binary", media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": Ext("binary", media="application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".doc": Ext("binary", media="application/msword"),
    ".xls": Ext("binary", media="application/vnd.ms-excel"),
    ".ppt": Ext("binary", media="application/vnd.ms-powerpoint"),
    ".odt": Ext("binary", media="application/vnd.oasis.opendocument.text"),
    ".ods": Ext("binary", media="application/vnd.oasis.opendocument.spreadsheet"),
    ".zip": Ext("binary", media="application/zip"),
    ".gz": Ext("binary", media="application/gzip"),
    ".tgz": Ext("binary", media="application/gzip"),
    ".tar": Ext("binary", media="application/x-tar"),
    ".7z": Ext("binary", media="application/x-7z-compressed"),
    ".whl": Ext("binary", media="application/zip"),
    ".h5": Ext("binary", media="application/x-hdf5"),
    ".hdf5": Ext("binary", media="application/x-hdf5"),
}

# Whole names (lowercased) that say what a file is without an extension.
NAMES: dict[str, tuple[str, str]] = {
    **{n: ("text", "makefile") for n in ("makefile", "gnumakefile", "justfile")},
    **{n: ("text", "dockerfile") for n in ("dockerfile", "containerfile")},
    "cmakelists.txt": ("text", "cmake"),
    **{n: ("text", "ruby") for n in ("rakefile", "gemfile")},
    **{n: ("text", "plaintext") for n in ("license", "licence", "copying", "readme", "authors", "changelog",
                                          ".gitignore", ".gitattributes", ".gammaignore", ".dockerignore")},
    ".editorconfig": ("text", "ini"),
    ".env": ("text", "shell"),
}

# The serving views of the table.
IMAGE_MEDIA_TYPES: dict[str, str] = {e: x.media for e, x in EXTENSIONS.items() if x.format == "image"}
IMAGE_EXTENSIONS: dict[str, str] = {}
for _ext, _media in IMAGE_MEDIA_TYPES.items():
    IMAGE_EXTENSIONS.setdefault(_media, _ext)  # the first row with a media type is its extension
ALLOWED_IMAGE_TYPES = frozenset(IMAGE_EXTENSIONS)
INLINE_EXTENSIONS = frozenset(e for e, x in EXTENSIONS.items() if x.inline)
SANDBOXED_EXTENSIONS = frozenset(e for e, x in EXTENSIONS.items() if x.sandboxed)

SNIFF_BYTES = 8192  # what sniff reads of a file


def extension_of(name: str) -> str:
    """A name's last suffix, lowercased (``Data.CSV`` → ``.csv``;
    ``pkg.tar.gz`` → ``.gz``): ``""`` when there is none (no dot after the
    first character: ``.gitignore`` has none) or it is malformed
    (``EXTENSION_RE``). Blocked ones are answered like any other."""
    name = str(name or "")
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot > 0 else ""
    return ext if EXTENSION_RE.match(ext) else ""


def stored_extension(name: str) -> str:
    """The extension a file named ``name`` is stored under: its
    ``extension_of``, ``.bin`` when it has none. Raises ValueError for
    BLOCKED_EXTENSIONS."""
    ext = extension_of(name)
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"{ext} files are not accepted (executable)")
    return ext or ".bin"


def media_type(ext: str) -> str | None:
    """The media type a stored file with extension ``ext`` (lowercase, with
    the dot) is served as, or None when Gamma never stores that kind of
    file (blocked, malformed). Unknown but well-formed extensions are
    ``application/octet-stream``."""
    if ext in BLOCKED_EXTENSIONS or not EXTENSION_RE.match(ext or ""):
        return None
    row = EXTENSIONS.get(ext)
    return (row.media if row else None) or "application/octet-stream"


def format_of(name: str, head: bytes | None = None) -> tuple[str, str | None]:
    """``(format, language)`` of a file named ``name``: its whole name
    (``NAMES``), else its extension (``EXTENSIONS``), else what its first
    bytes say when ``head`` is given (``sniff``), else ``("binary",
    None)``."""
    leaf = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if leaf in NAMES:
        return NAMES[leaf]
    row = EXTENSIONS.get(extension_of(name))
    if row:
        return row.format, row.language
    if head is not None:
        return sniff(head)
    return "binary", None


def language_of(name: str, ext: str) -> str | None:
    """The language a text document's name or extension says (``NAMES``,
    then a text row of ``EXTENSIONS``), else None."""
    known = NAMES.get(str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower())
    if known:
        return known[1]
    row = EXTENSIONS.get(ext)
    return row.language if row and row.format == "text" else None


def sniff(head: bytes) -> tuple[str, str | None]:
    """What a file the table does not know is, by its first bytes (at most
    ``SNIFF_BYTES`` are read): a PDF header at the very start is ``pdf``; a
    NUL byte, or bytes that are no UTF-8, are ``binary``; anything else,
    nothing included, is plain text. A multi-byte sequence cut by the limit
    at the very end is UTF-8 still."""
    head = bytes(head[:SNIFF_BYTES])
    if head.startswith(b"%PDF-"):
        return "pdf", None
    if b"\x00" in head:
        return "binary", None
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=len(head) < SNIFF_BYTES)
    except UnicodeDecodeError:
        return "binary", None
    return "text", "plaintext"
