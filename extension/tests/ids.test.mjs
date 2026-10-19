import assert from "node:assert/strict";
import { test } from "node:test";
import "../ids.js";

const { gammaArxivId: arxivId, gammaDoiFromPath: doiFromPath, gammaCleanDoi: cleanDoi, gammaTextDois: textDois } = globalThis;

test("a DOI loses what a sentence or a link puts after it, and keeps its own brackets", () => {
  const cases = {
    "doi:10.1038/nature12373.": "10.1038/nature12373",
    "(see 10.1103/PhysRevLett.116.061102), and": "10.1103/PhysRevLett.116.061102",
    "10.1016/S0006-3495(00)76579-8;": "10.1016/S0006-3495(00)76579-8",
    "[10.1007/978-3-319-24277-4_9]": "10.1007/978-3-319-24277-4_9",
    "https://doi.org/10.1002%2Fanie.201915678?via=x": "10.1002/anie.201915678",
    "no identifier here": "",
  };
  for (const [text, doi] of Object.entries(cases)) assert.equal(cleanDoi(text), doi, text);
});

test("the DOIs a page prints or links to, each once", () => {
  // A news story: the DOI in its text and its doi.org link are one paper.
  assert.deepEqual(textDois("The study (doi:10.1038/s41586-024-07107-7) shows…",
    ["https://doi.org/10.1038/S41586-024-07107-7", "https://example.org/about"]), ["10.1038/s41586-024-07107-7"]);
  // A link alone counts; a DOI-shaped path on another site does not.
  assert.deepEqual(textDois("Read the paper.", ["https://dx.doi.org/10.1126/science.abc1234"]), ["10.1126/science.abc1234"]);
  assert.deepEqual(textDois("", ["https://journal.example/doi/10.1126/science.abc1234"]), []);
  // A reference list names several, up to the cap.
  const refs = Array.from({ length: 14 }, (_, i) => `[${i + 1}] doi:10.1000/ref.${i + 1}`).join("\n");
  assert.equal(textDois(refs).length, 10);
  assert.deepEqual(textDois("10.1/a and 10.1000/b", [], 5), ["10.1000/b"]);  // a prefix needs four digits
});

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
