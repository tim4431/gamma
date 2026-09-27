import assert from "node:assert/strict";
import { test } from "node:test";
import "../ids.js";

const { gammaArxivId: arxivId, gammaDoiFromPath: doiFromPath } = globalThis;

test("arXiv ids from abs, pdf and html URLs, old-style ids included", () => {
  const cases = {
    "https://arxiv.org/abs/2310.06825v2": "2310.06825",
    "https://arxiv.org/pdf/2310.06825": "2310.06825",
    "https://arxiv.org/html/2310.06825v1#S2": "2310.06825",
    "https://arxiv.org/html/cond-mat/0402216": "cond-mat/0402216",
    "https://arxiv.org/pdf/math.GT/0309136v1": "math.GT/0309136",
    "arXiv:hep-th/9901001": "hep-th/9901001",
    "2310.06825v3": "2310.06825",        // citation_arxiv_id
    "cond-mat/0402216": "cond-mat/0402216",
    "https://example.org/html/2310.06825": "",
    "": "",
  };
  for (const [text, id] of Object.entries(cases)) assert.equal(arxivId(text), id, text);
});

test("a DOI used as a URL path", () => {
  assert.equal(doiFromPath("/prl/pdf/10.1103/PhysRevLett.115.137002"), "10.1103/PhysRevLett.115.137002");
  assert.equal(doiFromPath("/content/pdf/10.1007/s11433-020-1234-5.pdf"), "10.1007/s11433-020-1234-5");
  assert.equal(doiFromPath("/html/cond-mat/0402216"), "");
});
