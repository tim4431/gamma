// A word diff of two texts, git's --word-diff: the conflict cards of a
// clone (collaboration/MergeResolver.jsx) and the review card of a
// highlight placed after its PDF was replaced (pdf/highlightReview.js).
// Pure, so node tests it through its users.

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
