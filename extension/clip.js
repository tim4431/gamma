// Content script, injected on demand after lib/Readability.js (worker.js
// clipTab): the tab as a web clip — its article, found as Firefox's Reader
// View, Joplin and SiYuan find one (Mozilla's Readability, run on a copy of
// the page), or the text selected on it — as a clean HTML fragment for the
// server to make into note blocks (gamma/web_clip.py). What only the live
// page knows is written onto the copy first, or Readability would lose it:
// an image's real address (a lazy one's, the one a srcset shows), a
// formula's TeX or MathML, a code block's language. Nothing is sent from
// here; the worker asks, uploads the pictures and saves.

(() => {
  if (globalThis.gammaClip) return;

  // The attributes lazy loaders keep an image's real address in.
  const LAZY = ["data-src", "data-original", "data-lazy-src", "data-actualsrc", "data-original-src",
                "data-hi-res-src", "data-url"];
  const MAX_HTML = 4 * 1024 * 1024;

  function absolute(url) {
    try { return new URL(url, document.baseURI).href; } catch { return ""; }
  }

  // A stand-in a lazy loader shows until the image scrolls into view.
  function placeholder(src) {
    return !src || /^about:/i.test(src) || (/^data:/i.test(src) && src.length < 600);
  }

  // The largest candidate of a srcset ("a.jpg 640w, b.jpg 1280w").
  function largest(srcset) {
    let best = "", size = -1;
    for (const part of String(srcset || "").split(/,\s+(?=\S)/)) {
      const [url, desc = "1x"] = part.trim().split(/\s+/);
      const n = parseFloat(desc) || 1;
      if (url && n > size) { best = url; size = n; }
    }
    return best;
  }

  // Where an image's picture is: what the browser shows, unless that is a
  // lazy loader's stand-in, then what the loader keeps, then the srcset.
  function imageSource(img, live) {
    if (live && live.currentSrc && !placeholder(live.currentSrc) && live.naturalWidth > 0) return live.currentSrc;
    for (const name of LAZY) {
      const value = img.getAttribute(name);
      if (value && !placeholder(value)) return value;
    }
    const set = img.getAttribute("data-srcset") || img.getAttribute("srcset")
      || img.parentElement?.tagName === "PICTURE" && img.parentElement.querySelector("source[srcset]")?.getAttribute("srcset");
    return largest(set) || img.getAttribute("src") || "";
  }

  // An emoji drawn as a picture (Twitter's, WordPress's) reads as itself.
  function emoji(img, live) {
    const alt = img.getAttribute("alt") || "";
    const small = live ? live.naturalWidth > 0 && live.naturalWidth <= 72 : /^\d\d?$/.test(img.getAttribute("width") || "");
    return alt && [...alt].length <= 4 && (/\bemoji\b/i.test(img.className) || (small && /\p{Extended_Pictographic}/u.test(alt)));
  }

  function fixImages(root, liveImages) {
    const imgs = [...root.querySelectorAll("img")];
    imgs.forEach((img, i) => {
      const live = liveImages && liveImages[i];
      if (emoji(img, live)) { img.replaceWith(img.ownerDocument.createTextNode(img.getAttribute("alt"))); return; }
      const src = imageSource(img, live);
      if (!src || placeholder(src) && !/^data:image\//i.test(src)) { img.remove(); return; }
      // A tracking pixel: the picture itself is a dot (not a lazy loader's
      // stand-in for one still to come).
      if (live && live.complete && live.currentSrc === absolute(src) && live.naturalWidth > 0
          && live.naturalWidth <= 2 && live.naturalHeight <= 2) { img.remove(); return; }
      img.setAttribute("src", /^data:/i.test(src) ? src : absolute(src));
      for (const name of ["srcset", "sizes", "loading", "data-srcset", ...LAZY]) img.removeAttribute(name);
    });
    for (const source of root.querySelectorAll("picture source")) source.remove();
  }

  // A formula as one span the server reads (web_clip.py): its TeX when the
  // page kept it, else its MathML, the text it shows in between so the
  // article's length counts it.
  function mathSpan(doc, display, tex, mathmlSource, text) {
    const span = doc.createElement("span");
    span.setAttribute("data-gamma-math", display ? "display" : "inline");
    if (tex) span.setAttribute("data-tex", tex.trim());
    else if (mathmlSource) span.setAttribute("data-mathml", mathmlSource);
    span.textContent = (tex || text || "").trim() || "math";
    return span;
  }

  const TEX = 'annotation[encoding="application/x-tex"]';

  function fixMath(root) {
    const doc = root.ownerDocument || root;
    // KaTeX: the TeX rides in the MathML it renders beside the glyphs.
    for (const el of [...root.querySelectorAll(".katex-display, .katex")]) {
      if (!el.isConnected || el.parentElement?.closest(".katex-display, .katex")) continue;
      const tex = el.querySelector(TEX)?.textContent || "";
      if (tex) el.replaceWith(mathSpan(doc, el.classList.contains("katex-display"), tex, "", ""));
    }
    // MathJax 2 keeps the TeX in a script after the rendered spans.
    for (const el of root.querySelectorAll(".MathJax_Preview, .MathJax, .MathJax_Display, .MathJax_SVG, .MathJax_SVG_Display, .MathJax_CHTML")) el.remove();
    for (const script of [...root.querySelectorAll('script[type^="math/tex"]')]) {
      script.replaceWith(mathSpan(doc, /mode\s*=\s*display/i.test(script.type), script.textContent, "", ""));
    }
    // MathJax 3 keeps MathML for screen readers, and no TeX.
    for (const el of [...root.querySelectorAll("mjx-container")]) {
      const math = el.querySelector("mjx-assistive-mml math, math");
      const tex = math?.querySelector(TEX)?.textContent || "";
      el.replaceWith(mathSpan(doc, el.getAttribute("display") === "true", tex, math ? math.outerHTML : "", el.textContent));
    }
    // Wikipedia draws a picture of each formula over its MathML.
    for (const el of [...root.querySelectorAll(".mwe-math-element")]) {
      const math = el.querySelector("math");
      const tex = math?.querySelector(TEX)?.textContent || math?.getAttribute("alttext")
        || el.querySelector("img.mwe-math-fallback-image-inline, img.mwe-math-fallback-image-display")?.getAttribute("alt") || "";
      const display = !!el.querySelector(".mwe-math-mathml-display, .mwe-math-fallback-image-display");
      el.replaceWith(mathSpan(doc, display, tex, math ? math.outerHTML : "", ""));
    }
    // Any other MathML (arXiv's HTML papers, LaTeXML).
    for (const math of [...root.querySelectorAll("math")]) {
      if (!math.isConnected) continue;
      const tex = math.querySelector(TEX)?.textContent || math.getAttribute("alttext") || "";
      math.replaceWith(mathSpan(doc, math.getAttribute("display") === "block", tex, math.outerHTML, ""));
    }
  }

  // A code block's language from its classes (language-js, lang-py, GitHub's
  // highlight-source-python on the wrapper), and its text without the
  // highlighter's spans or a line-number gutter.
  function fixCode(root) {
    for (const pre of root.querySelectorAll("pre")) {
      const classes = [pre, pre.querySelector("code"), pre.parentElement].filter(Boolean).map((el) => el.className || "").join(" ");
      const m = /(?:^|\s)(?:language|lang|highlight-source|brush:)-?\s*([\w+#.-]+)/i.exec(classes);
      for (const gutter of pre.querySelectorAll(".linenos, .lineno, .line-numbers-rows, .gutter, [data-line-number]")) gutter.remove();
      const text = pre.textContent;
      pre.replaceChildren(pre.ownerDocument.createTextNode(text));
      if (m) pre.setAttribute("data-lang", m[1].toLowerCase());
    }
  }

  // Links and pictures by their full address, which a fragment taken out of
  // the page no longer resolves against.
  function fixLinks(root) {
    for (const a of root.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href");
      if (!href.startsWith("#")) a.setAttribute("href", absolute(href));
    }
  }

  // The pictures a fragment shows: its own address for each, the data: ones
  // swapped for a token (`gamma-inline:<n>`) so the fragment stays small and
  // the worker uploads the bytes.
  function pictures(html) {
    const t = document.createElement("template");
    t.innerHTML = html;
    const images = [];
    const inline = [];
    for (const img of t.content.querySelectorAll("img[src]")) {
      const src = img.getAttribute("src");
      if (/^data:image\//i.test(src)) {
        img.setAttribute("src", `gamma-inline:${inline.length}`);
        inline.push(src);
      } else if (/^https?:/i.test(src) && !images.includes(src)) images.push(src);
    }
    return { html: inline.length ? t.innerHTML : html, images, inline };
  }

  // A title without the site's name after it ("Ticks — Clock Blog"):
  // Readability splits a title on "|", "-", "/", ">" and "»", not on the
  // dashes and the dot many sites use.
  function withoutSite(title, site) {
    const escaped = site.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const rest = site ? title.replace(new RegExp(`\\s+[–—·•|:-]\\s+${escaped}$`, "i"), "") : title;
    return rest.trim() || title;
  }

  function article() {
    const copy = document.cloneNode(true);
    fixImages(copy, [...document.querySelectorAll("img")]);
    fixMath(copy);
    fixCode(copy);
    const read = new Readability(copy, { charThreshold: 200 }).parse();
    if (!read || !read.content || (read.textContent || "").trim().length < 40) return { html: "" };
    const site = (read.siteName || "").trim();
    return { html: read.content, title: withoutSite((read.title || "").trim(), site), byline: (read.byline || "").trim(),
             site, published: (read.publishedTime || "").trim() };
  }

  function selection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return { html: "" };
    const box = document.createElement("div");
    for (let i = 0; i < sel.rangeCount; i++) {
      const range = sel.getRangeAt(i);
      // A selection inside a code block or a list keeps being one.
      const holder = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
      const pre = holder && holder.closest("pre");
      const list = !pre && holder && /^[OU]L$/.test(holder.tagName) ? holder : null;
      let part = range.cloneContents();
      if (pre || list) {
        const wrap = document.createElement(pre ? "pre" : list.tagName.toLowerCase());
        if (pre) wrap.className = [pre.className, pre.querySelector("code")?.className, pre.parentElement?.className].filter(Boolean).join(" ");
        wrap.append(part);
        part = wrap;
      }
      box.append(part);
    }
    if (!box.textContent.trim() && !box.querySelector("img")) return { html: "" };
    fixImages(box, null);
    fixMath(box);
    fixCode(box);
    fixLinks(box);
    for (const el of box.querySelectorAll("script, style, noscript, template, button, input, select, textarea")) el.remove();
    return { html: box.innerHTML };
  }

  // `mode`: "article" or "selection" → {html, title, byline, site,
  // published, images, inline} (html "" when there is nothing to clip).
  globalThis.gammaClip = (mode) => {
    const got = mode === "selection" ? selection() : article();
    if (!got.html) return { mode, html: "", images: [], inline: [] };
    const pics = pictures(got.html);
    if (pics.html.length > MAX_HTML) return { mode, html: "", images: [], inline: [], error: "the page is too long to clip" };
    return { ...got, mode, ...pics };
  };
})();
