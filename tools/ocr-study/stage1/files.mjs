// The study's settings: where the corpus and the outputs live, and the repo
// code the evaluator loads. Nothing here is a local path: the working
// directory is $OCR_STUDY_DIR (default tools/ocr-study/.work), the corpus
// PDFs are $OCR_STUDY_DIR/corpus/<id>.pdf (ids from ../corpus.json), and the
// repo paths are resolved from this file's location.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..", "..");

export const WORK = resolve(process.env.OCR_STUDY_DIR || join(HERE, "..", ".work"));
const CORPUS = JSON.parse(readFileSync(join(HERE, "..", "corpus.json"), "utf8"));
export const FILES = Object.fromEntries(Object.keys(CORPUS.files).map((id) => [id, join(WORK, "corpus", `${id}.pdf`)]));
export const PDFJS = join(REPO, "frontend", "node_modules", "pdfjs-dist", "legacy", "build", "pdf.mjs");
export const SRC = join(REPO, "frontend", "src", "pdf");
export const PAGES = join(HERE, "pages.json"); // the 46-page sample
export const STUDY = join(WORK, "stage1"); // words/, truth/, overlays/
