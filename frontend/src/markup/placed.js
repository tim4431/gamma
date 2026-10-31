// What a text box and a picture share as objects placed on a page surface
// (markup/useTextBoxes.js, markup/usePictures.js): the tree edit that
// changes the property placing one, and where a new one goes. Pure, so it
// runs under node --test.
import { findBlock, updateBlockTree } from "../shared/model/blockModel.js";
import { PLACED } from "../shared/model/blockOps.js";

// `tree` with block `id`'s `key` ("text_box" or "picture", blockOps.js
// PLACED) replaced by fn(its value, normalized); the same tree when that
// changes nothing, or when the block holds no such value.
export function withPlaced(tree, id, key, fn) {
  const was = PLACED[key].normalize(findBlock(tree, id)?.properties?.[key]);
  const next = was && fn(was);
  if (!next || JSON.stringify(next) === JSON.stringify(was)) return tree;
  return updateBlockTree(tree, id, (b) => ({ ...b, properties: { ...b.properties, [key]: next } }));
}

// `tree` with a new block `id` of `properties` (its t0 and its `text_box` or
// `picture`) on `surface`, a PDF page number or a sheet id, its content
// empty: on a PDF page at the page's top level with its pdf_page, on a
// sheet as the sheet's last child. The same tree when that sheet is gone.
export function addPlaced(tree, surface, id, properties) {
  const onSheet = typeof surface === "string";
  const node = { id, parentId: onSheet ? surface : null, children: [], content: "",
    properties: onSheet ? properties : { ...properties, pdf_page: surface } };
  if (!onSheet) return [...tree, node];
  return findBlock(tree, surface) ? updateBlockTree(tree, surface, (s) => ({ ...s, children: [...(s.children || []), node] })) : tree;
}
