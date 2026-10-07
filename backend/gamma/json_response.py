"""The JSON answer of a handler whose body can be large, encoded by orjson
in the handler's worker thread.

A dict returned from a route is encoded by FastAPI on the event loop
(``jsonable_encoder`` and the standard ``json.dumps``): a megabyte of
tree, conversation or op log stalls every socket and request for the
tens of milliseconds that takes. A handler that builds this response
instead encodes where it runs — the threadpool, for a sync def — and the
loop only sends bytes (docs/dev/api.md has the timings).

NaN and the infinities a stored row may hold go out as null (a write
refuses them now). A lone surrogate goes out as U+FFFD (``ops.storable``,
told to let the NaN through): UTF-8 has no encoding for one. What orjson
still refuses goes through the standard encoder: nesting past 255 levels
(a tree about 125 blocks deep), an integer past 64 bits.
"""

import orjson
from fastapi.responses import JSONResponse

from .ops import storable


class OrjsonResponse(JSONResponse):
    def render(self, content) -> bytes:
        try:
            return orjson.dumps(content)
        except orjson.JSONEncodeError:
            content = storable(content, finite=False)
        try:
            return orjson.dumps(content)
        except orjson.JSONEncodeError:
            return super().render(content)
