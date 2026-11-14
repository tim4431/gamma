import test from "node:test";
import assert from "node:assert/strict";

// The routing of a file on its way to being a page's document
// (src/transfers/documentUploads.js, whose pure rules are
// src/transfers/fileRouting.js: the requests beside them go through
// shared/lib/utils.js, which needs the browser): what a file becomes, and
// what a dropped directory leaves out.
import { ASK_PAST_FILES, dropFilter, routeFile, uploadName } from "../src/transfers/fileRouting.js";

const file = (name, type = "") => ({ name, type });

test("an upload's name is the file's whole: a slash in it is no directory, only a picker's leaked path is", () => {
  assert.equal(uploadName(file("AC/DC live.pdf")), "AC/DC live.pdf");
  assert.equal(uploadName(file(" I/O notes.md ")), "I/O notes.md");
  assert.equal(uploadName(file("back\\slash.txt")), "back\\slash.txt");
  const picked = (name, webkitRelativePath) => ({ name, webkitRelativePath });
  assert.equal(uploadName(picked("x.pdf", "papers/readout/x.pdf")), "x.pdf");
  assert.equal(uploadName(picked("papers/readout/x.pdf", "papers/readout/x.pdf")), "x.pdf", "the path leaked into the name");
  assert.equal(uploadName(picked("", "papers/x.pdf")), "x.pdf", "a name only the path gives");
  assert.equal(uploadName(picked("AC/DC.pdf", "papers/x.pdf")), "AC/DC.pdf", "a name that is no leak stays whole");
  assert.equal(uploadName(file(""), "upload.pdf"), "upload.pdf");
  assert.equal(uploadName(null, "file"), "file");
});

test("what a file becomes: a PDF the PDF way, a Markdown file a note, any other a document, an executable nothing", () => {
  assert.equal(routeFile(file("paper.PDF")), "pdf");
  assert.equal(routeFile(file("download", "application/pdf")), "pdf");
  assert.equal(routeFile(file("notes.md")), "markdown");
  assert.equal(routeFile(file("notes.markdown")), "markdown");
  assert.equal(routeFile(file("README", "text/markdown")), "markdown");
  for (const name of ["fig.png", "main.tex", "data.json", "Makefile", "blob.bin", "weights.ckpt", "clip.mp4",
    "tone.wav", "run.log", "archive.zip", "noext", "photo.jpeg", "scan.tiff"]) {
    assert.equal(routeFile(file(name)), "file", name);
  }
  assert.equal(routeFile(file("scan.tiff", "image/tiff")), "file", "an image the page would take as a picture is a document here");
  for (const name of ["setup.exe", "run.bat", "x.ps1", "lib.dll", "Setup.MSI"]) {
    assert.equal(routeFile(file(name)), "", name);
  }
  assert.equal(routeFile(null), "file", "a file with no name is stored as .bin");
});

test("a dropped directory leaves out dot directories and files, node_modules, temporary names and executables", () => {
  const pair = (folder, name) => ({ file: file(name), folder });
  const rel = (path) => ({ name: path.split("/").pop(), webkitRelativePath: path });
  const kept = [
    pair(["repo"], "main.tex"), pair(["repo", "figures"], "fig.png"), pair(["repo"], "Makefile"),
    pair(["repo", "data"], "run.log"), rel("repo/src/model.py"), rel("repo/README"), file("loose.pdf"),
  ];
  const skipped = [
    pair(["repo", ".git"], "HEAD"), pair(["repo", ".git", "objects", "ab"], "cdef"), pair(["repo"], ".DS_Store"),
    pair(["repo"], ".gitignore"), pair(["repo", "node_modules", "left-pad"], "index.js"),
    pair(["repo"], "~$thesis.docx"), pair(["repo"], ".#notes.txt"), pair(["repo"], "notes.txt~"),
    pair(["repo"], "scratch.tmp"), pair(["repo"], ".main.tex.swp"), pair(["repo"], "run-local.bat"),
    pair(["repo"], "setup.exe"), rel("repo/.vscode/settings.json"), rel("repo/build/Thumbs.db.tmp"),
  ];
  const out = dropFilter([...kept, ...skipped]);
  assert.deepEqual(out.kept, kept);
  assert.deepEqual(out.skipped, skipped);
  assert.deepEqual(dropFilter([]), { kept: [], skipped: [] });
  assert.deepEqual(dropFilter(null), { kept: [], skipped: [] });
});

test("a name alone is judged by itself", () => {
  const { kept, skipped } = dropFilter([file("fig.png"), file(".hidden"), file("x.tmp")]);
  assert.deepEqual(kept.map((f) => f.name), ["fig.png"]);
  assert.deepEqual(skipped.map((f) => f.name), [".hidden", "x.tmp"]);
});

test("a directory drop asks past a hundred files", () => {
  assert.equal(ASK_PAST_FILES, 100);
});
