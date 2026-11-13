// A word diff of two texts, git's --word-diff: the conflict cards of a
// clone (collaboration/MergeResolver.jsx) and the review card of a
// highlight placed after its PDF was replaced (pdf/highlightReview.js);
// `diffIndices` is the step under the tracked changes a note shows of an
// AI's edit (editor/mdDiff.js). Pure, so node tests it through its users.

// Word-level tokens: runs of non-space and runs of space, so a diff never
// splits a word and the coloured spans keep their spacing.
export function tokens(s) {
  return (s || "").match(/\s+|[^\s]+/g) || [];
}

// The LCS table of two token arrays (a classic DP; block texts are short).
export function lcs(A, B) {
  const n = A.length, m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

// A git-style word diff from `a` to `b`: runs tagged "same", "del" (only
// in a) and "add" (only in b), whitespace kept.
export function wordDiff(a, b) {
  const A = tokens(a), B = tokens(b);
  const n = A.length, m = B.length;
  const dp = lcs(A, B);
  const out = [];
  const push = (text, tag) => {
    const prev = out[out.length - 1];
    if (prev && prev.tag === tag) prev.text += text;
    else out.push({ text, tag });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push(A[i], "same"); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push(A[i], "del"); i++; }
    else { push(B[j], "add"); j++; }
  }
  while (i < n) push(A[i++], "del");
  while (j < m) push(B[j++], "add");
  return out;
}

// Past this many cells the LCS table is not built: what differs goes out
// and comes in whole (4 MB of table).
const TABLE_MAX = 1_000_000;

// The diff of two token arrays one token at a time: ["same", i, j] (A[i]
// is B[j]), ["del", i, -1] (only in A), ["add", -1, j] (only in B), in
// order. The common head and tail are matched before the table, so an
// edit to one place of a long text costs that place; a middle too large
// for the table is all deleted, then all added.
export function diffIndices(A, B) {
  const n = A.length, m = B.length;
  let head = 0;
  while (head < n && head < m && A[head] === B[head]) head++;
  let tail = 0;
  while (tail < n - head && tail < m - head && A[n - 1 - tail] === B[m - 1 - tail]) tail++;
  const ops = [];
  for (let k = 0; k < head; k++) ops.push(["same", k, k]);
  const a = A.slice(head, n - tail), b = B.slice(head, m - tail);
  let i = 0, j = 0;
  if (a.length * b.length <= TABLE_MAX) {
    const dp = lcs(a, b);
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { ops.push(["same", head + i, head + j]); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push(["del", head + i, -1]); i++; }
      else { ops.push(["add", -1, head + j]); j++; }
    }
  }
  for (; i < a.length; i++) ops.push(["del", head + i, -1]);
  for (; j < b.length; j++) ops.push(["add", -1, head + j]);
  for (let k = 0; k < tail; k++) ops.push(["same", n - tail + k, m - tail + k]);
  return ops;
}
