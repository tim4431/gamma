// A derived map or object is rebuilt on every edit (the tree changes under
// it), while what it holds rarely does: these keep the previous value by
// identity while the next one reads the same, so a memoized consumer that
// takes it as a prop does not re-render for a keystroke elsewhere.

// `prevRef.current` when `equal(prev, next)`, else `next`; the kept value is
// written back to the ref.
export function keepIfSame(prevRef, next, equal) {
  const prev = prevRef.current;
  return (prevRef.current = prev && equal(prev, next) ? prev : next);
}

// Shallow equality of two Maps: the same keys, each value `same` (`===`).
export function sameMap(a, b, same = (x, y) => x === y) {
  return a.size === b.size && [...b].every(([k, v]) => same(a.get(k), v));
}

// Shallow equality of two plain objects: the same keys, each value `same`.
export function sameObject(a, b, same = (x, y) => x === y) {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => same(a[k], b[k]));
}
