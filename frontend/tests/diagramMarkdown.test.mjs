import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { diagramFence, diagramWidth, mermaidMath, normalizeChatMarkdown, remarkDiagrams, scanDiagramFences, setDiagramWidth } from "../src/shared/lib/diagramMarkdown.js";

const render = (text) => renderToStaticMarkup(React.createElement(ReactMarkdown, {
  remarkPlugins: [remarkDiagrams], children: text,
}));
const SVG = '<svg width="40" height="20"><rect width="40" height="20"/></svg>';

test("a diagram waits for a matching closing fence, including lists and quotes", () => {
  for (const [lang, body] of [["mermaid", "flowchart LR\nA-->B"], ["svg", SVG]]) {
    for (const prefix of ["", "> ", "  "]) {
      for (const fence of ["```", "~~~~"]) {
        const open = `${prefix}${fence}${lang}\n${body.split("\n").map((l) => prefix + l).join("\n")}`;
        assert.match(render(open), /data-diagram-pending="true"/);
        assert.match(render(`${open}\n${prefix}${fence}`), /data-diagram-pending="false"/);
        assert.match(render(open), new RegExp(`data-diagram-kind="${lang}"`));
      }
    }
  }
  assert.match(render("- ```mermaid\n  flowchart LR\n  A-->B\n  ```"), /data-diagram-pending="false"/);
  assert.match(render("````mermaid\nflowchart LR\nA-->B\n```"), /data-diagram-pending="true"/);
  assert.match(render("```SVG\n<svg/>\n```"), /data-diagram-kind="svg"/, "the language is matched in any case");
  assert.doesNotMatch(render("```js\nlet a = 1;\n```"), /data-diagram/);
  assert.doesNotMatch(render("```xml\n<svg/>\n```"), /data-diagram/, "only an svg fence is a drawing");
});

test("math normalization leaves all diagram and code source intact", () => {
  const source = 'flowchart LR\nA["\\(x\\) $a|b$ $$c$$ [[node]]"] --> B';
  for (const fence of ["```", "~~~", "````"]) {
    for (const prefix of ["", "> ", "  "]) {
      const md = `${fence}mermaid\n${source}\n${fence}`.split("\n").map((s) => prefix + s).join("\n");
      assert.equal(normalizeChatMarkdown(`\\(before\\)\n\n${md}\n\n$after|pipe$`), `$before$\n\n${md}\n\n$after\\vert pipe$`);
      assert.equal(normalizeChatMarkdown(md.slice(0, md.lastIndexOf("\n"))), md.slice(0, md.lastIndexOf("\n")));
    }
  }
  const svg = "```svg\n<svg><text>$5 and \\(x\\)</text></svg>\n```";
  assert.equal(normalizeChatMarkdown(svg), svg);
});

test("copy fences round-trip source containing backticks, in either kind", () => {
  const source = 'flowchart LR\nA["```literal```"] --> B';
  const md = diagramFence("mermaid", source);
  assert(md.startsWith("````mermaid\n"));
  assert.match(render(md), /data-diagram-pending="false"/);
  assert(render(md).includes("```literal```"));
  const svg = '<svg><text>```</text></svg>';
  assert.equal(diagramFence("svg", svg), "````svg\n" + svg + "\n````");
  assert.equal(diagramFence("svg", SVG), "```svg\n" + SVG + "\n```");
  assert.match(render(diagramFence("svg", svg)), /data-diagram-kind="svg" data-diagram-pending="false"/);
  assert.deepEqual(scanDiagramFences(diagramFence("svg", svg)).map((f) => [f.kind, f.closed]), [["svg", true]]);
});

test("a diagram's width lives in the fence info string and rides into the HTML", () => {
  assert.equal(diagramWidth("width=420"), 420);
  assert.equal(diagramWidth("theme=x width=420 other"), 420);
  assert.equal(diagramWidth("widths=1 xwidth=2"), null);
  assert.equal(diagramWidth(""), null);
  assert.match(render("```mermaid width=300\nflowchart LR\nA-->B\n```"), /data-diagram-width="300"/);
  assert.match(render("```svg width=250\n" + SVG + "\n```"), /data-diagram-width="250"/);
  assert.doesNotMatch(render("```mermaid\nflowchart LR\nA-->B\n```"), /data-diagram-width/);
  assert.doesNotMatch(render("```svg\n" + SVG + "\n```"), /data-diagram-width/);
});

test("setDiagramWidth rewrites only the nth diagram's opening line", () => {
  const note = [
    "intro",
    "```js\nconst a = 1;\n```",
    "```mermaid\nflowchart LR\nA-->B\n```",
    "> ```mermaid width=200 theme=dark\n> flowchart LR\n> C-->D\n> ```",
    "~~~~Mermaid\nsequenceDiagram\n~~~~",
  ].join("\n\n");
  const fences = scanDiagramFences(note);
  assert.deepEqual(fences.map((f) => [f.kind, f.lang, f.width, f.prefix]),
    [["mermaid", "mermaid", null, ""], ["mermaid", "mermaid", 200, "> "], ["mermaid", "Mermaid", null, ""]]);
  const sized = setDiagramWidth(note, 0, 480);
  assert.match(sized, /\n```mermaid width=480\nflowchart LR\nA-->B\n```/);
  assert.equal(sized.replace("```mermaid width=480", "```mermaid"), note, "nothing else changes");
  // A quoted fence keeps its prefix and the rest of its meta; 0 clears.
  assert.match(setDiagramWidth(note, 1, 333), /> ```mermaid theme=dark width=333\n/);
  assert.match(setDiagramWidth(note, 1, 0), /> ```mermaid theme=dark\n> flowchart/);
  assert.match(setDiagramWidth(note, 2, 50), /~~~~Mermaid width=50\nsequenceDiagram/);
  assert.equal(setDiagramWidth(note, 3, 100), null, "a stale index edits nothing");
  // A fence inside another fence's code is not a diagram.
  assert.equal(scanDiagramFences("````md\n```mermaid\nx\n```\n````").length, 0);
  assert.equal(scanDiagramFences("````md\n```svg\n<svg/>\n```\n````").length, 0);
});

test("both kinds share one index in source order", () => {
  const note = [
    "```svg\n" + SVG + "\n```",
    "```mermaid width=120\nflowchart LR\nA-->B\n```",
    "```js\nconst a = 1;\n```",
    "- ```svg width=90\n  " + SVG + "\n  ```",
    "```svg\n<svg>",
  ].join("\n\n");
  const fences = scanDiagramFences(note);
  assert.deepEqual(fences.map((f) => [f.kind, f.width, f.closed]),
    [["svg", null, true], ["mermaid", 120, true], ["svg", 90, true], ["svg", null, false]]);
  assert.equal(fences[3].end, note.length, "an open fence runs to the end");
  // The rendered order matches: the nth data-diagram-kind is the nth fence.
  assert.deepEqual([...render(note).matchAll(/data-diagram-kind="(\w+)"/g)].map((m) => m[1]), fences.map((f) => f.kind));
  const sized = setDiagramWidth(note, 0, 300);
  assert.equal(sized, note.replace("```svg\n" + SVG, "```svg width=300\n" + SVG), "index 0 is the first svg fence");
  assert.match(setDiagramWidth(note, 1, 0), /\n```mermaid\nflowchart LR/, "index 1 is the mermaid fence");
  assert.equal(setDiagramWidth(sized, 1, 0).split("\n")[0], "```svg width=300", "the svg fence keeps its width");
  assert.match(setDiagramWidth(note, 2, 45), /- ```svg width=45\n {2}<svg/, "a list item's fence keeps its marker");
  assert.equal(setDiagramWidth(note, 4, 45), null);
});

test("note-style $…$ labels become the $$…$$ Mermaid typesets", () => {
  assert.equal(mermaidMath(String.raw`flowchart LR
  S(($S_z$)) -.-|$\chi$| A(($a_1$))`), String.raw`flowchart LR
  S(($$S_z$$)) -.-|$$\chi$$| A(($$a_1$$))`);
  assert.equal(mermaidMath(String.raw`A[$a_2:|\alpha\rangle$]`), String.raw`A[$$a_2:|\alpha\rangle$$]`);
  // Already Mermaid's form, escaped dollars, prices and spaced dollars stay put.
  for (const same of ["A[$$x$$] --> B", String.raw`A[costs \$5]`, "A[$5 and $6]", "A[$ x $]", "A[$a\nb$]", "A[$$]"]) {
    assert.equal(mermaidMath(same), same);
  }
});
