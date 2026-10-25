// Pictures, App's side (docs/dev/pictures.md): which picture is selected
// and whether it is being cropped (one selection in the whole app: a
// picture, a text box or the lasso's strokes), the picture a jump outlines,
// and every edit of a picture. A picture is a block, so each edit is a tree
// edit through setBlocks: the op diff sends it, the merge with what another
// client changed meanwhile sees it, the block history records it. New
// pictures come from the strip's Picture button (the system file picker,
// shared/lib/pickFiles.js), a paste with an image on the clipboard while no
// text field has the focus, and image files dropped on a surface
// (markup/PictureLayer.jsx): each is uploaded as it is or re-encoded first
// (markup/pictureUpload.js), placed on its surface and selected, unless the
// selection moved on while it went up. App wires the actions into
// PageToolsContext and the state into the page marks;
// markup/PictureLayer.jsx draws the pictures.
import { useEffect, useMemo, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { copyPng } from "../shared/lib/copyImage.js";
import { isTextField } from "../shared/lib/hotkeys.js";
import { pickFiles } from "../shared/lib/pickFiles.js";
import { assetUrl } from "../shared/lib/utils.js";
import { findBlock, insertSibling, makeBlockId, removeBlockTree } from "../shared/model/blockModel.js";
import { wallNow } from "../shared/model/wallClock.js";
import { DUPLICATE_PX, surfaceUnits } from "../ink/surfaceScale.js";
import { postFile } from "../transfers/FileChip.jsx";
import { movePicture, normalizePicture, orderPicture, picturesBySurface, placePicture } from "./picture.js";
import { imageFiles, shownPng, uploadPicture } from "./pictureUpload.js";
import { addPlaced, withPlaced } from "./placed.js";

// `tree` with picture `id` replaced by fn(its picture), the same tree when
// that changes nothing or it is no picture that draws (markup/placed.js).
const withPicture = (tree, id, fn) => withPlaced(tree, id, "picture", fn);

// Whether picture `id` of `tree` is on a surface, and so drawn.
const drawn = (bySurface, id) => [...bySurface.values()].some((list) => list.some((b) => b.id === id));

// The pill for an upload that went wrong (pictureUpload.js uploadPicture).
const UPLOAD_ERRORS = {
  decode: () => t("This picture can't be read here: convert it to JPEG or PNG first."),
  upload: () => t("The picture could not be uploaded."),
};

// blocks, blocksRef, setBlocks: the open page's tree. readOnly: nothing is
// added or edited. pageId: the open page. tool: the strip's armed tool (a
// tool armed lets go of the picture, as it does of a box). pasteTarget():
// the surface in view and its size (ink/useInkSurfaces.js), where a pick
// or a paste lands. setStatus: the status pill. onSelect(): a picture was
// selected (App lets go of a text box and of the lasso's selection).
export function usePictures({ blocks, blocksRef, setBlocks, readOnly, pageId, tool, pasteTarget, setStatus, onSelect }) {
  // What a paste or an upload that finishes later reads: the latest render's.
  const live = useRef(null);
  live.current = { readOnly, pageId, pasteTarget, setStatus, onSelect };
  // {id, cropping}: the selected picture, and whether its crop mode is on.
  const [sel, setSelState] = useState(null);
  const selRef = useRef(null);
  // Counts the changes of the app's one selection: a picture selected or
  // let go here, a tool armed, and (App calls deselect) a box selected or
  // opened or the lasso's strokes selected. An upload that lands takes the
  // selection only when the count is where it was as the upload began.
  const turnRef = useRef(0);
  const setSel = (next) => {
    turnRef.current++;
    selRef.current = next;
    setSelState(next);
    if (next) live.current.onSelect();
  };
  const [flash, setFlash] = useState(null);
  const bySurface = useMemo(() => picturesBySurface(blocks), [blocks]);

  // A picture that no longer draws (undone, deleted, moved off its
  // surface, here or by someone else) is not selected; nor is any once a
  // tool is armed or another page opens.
  useEffect(() => {
    if (selRef.current && !drawn(bySurface, selRef.current.id)) setSel(null);
  }, [bySurface]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    turnRef.current++;
    if (tool && selRef.current) setSel(null);
  }, [tool]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (selRef.current) setSel(null); }, [pageId]); // eslint-disable-line react-hooks/exhaustive-deps

  // A new picture on `surface` (a PDF page number or a sheet id): a PDF
  // page's sits at the page's top level with its pdf_page, a sheet's is the
  // sheet's last child (markup/placed.js addPlaced), as a text box's is; its
  // caption empty; selected when `select`.
  function create(surface, picture, select) {
    const id = makeBlockId();
    const properties = { t0: wallNow(), picture };
    setBlocks((prev) => addPlaced(prev, surface, id, properties));
    if (select) setSel({ id, cropping: false });
  }

  // `files` uploaded one after another and placed on `surface` ({width,
  // height} `size` points): at their natural aspect, fitted inside half the
  // surface, centred on `at` (points) or on the surface (placePicture),
  // each next one DUPLICATE_PX screen px right of and below the one before.
  // Each is selected as it lands, unless the selection changed while it
  // went up (the user selected or opened something else, armed a tool or
  // let go of the picture before) or a field has the focus (a box's editor
  // would close): then it and the rest land unselected. A file that cannot
  // go up says why.
  async function place(files, surface, size, at) {
    const page = live.current.pageId;
    if (live.current.readOnly || !page || !size) return;
    const step = surfaceUnits(surface, DUPLICATE_PX);
    let placed = 0, turn = turnRef.current;
    for (const file of files) {
      const up = await uploadPicture(file, postFile);
      if (live.current.pageId !== page) return; // another page opened meanwhile
      if (up.error) { live.current.setStatus(UPLOAD_ERRORS[up.error]()); continue; }
      const frame = { url: up.url, ...placePicture(size, up.natural, at) };
      const select = turn === turnRef.current && !isTextField(document.activeElement);
      create(surface, placed ? movePicture(frame, step * placed, step * placed, size) : frame, select);
      turn = select ? turnRef.current : null;
      placed++;
    }
  }

  // A paste with image files on the clipboard while no text field (a
  // block's editor among them) has the focus and a surface is in view: the
  // images at the surface's centre. The handwriting clipboard's own copy
  // (ink/useInkSelection.js, marked data-gamma-ink) is the strip's paste.
  useEffect(() => {
    if (readOnly || !pageId) return undefined;
    const onPaste = (e) => {
      if (e.defaultPrevented || isTextField(e.target) || isTextField(document.activeElement)) return;
      const files = imageFiles(e.clipboardData);
      if (!files.length || (e.clipboardData.getData("text/html") || "").includes("data-gamma-ink")) return;
      const target = live.current.pasteTarget();
      if (!target) return;
      e.preventDefault();
      place(files, target.page, target.size, null);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [readOnly, pageId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Picture `id` becomes fn(its picture) (markup/picture.js movePicture,
  // scalePicture, cropTo …).
  function change(id, fn) {
    if (!readOnly) setBlocks((prev) => withPicture(prev, id, fn));
  }

  const actions = {
    onPictureSelect(id) {
      if (selRef.current?.id !== id) setSel({ id, cropping: false });
    },
    onPictureDeselect() { if (selRef.current) setSel(null); },
    // Crop mode on (the menu's Crop, a double click) and off again.
    onPictureCrop(id) {
      if (!readOnly && !selRef.current?.cropping) setSel({ id, cropping: true });
    },
    onPictureCropEnd(id) {
      if (selRef.current?.id === id && selRef.current.cropping) setSel({ id, cropping: false });
    },
    // A move, a handle, the menu's flips, Fit, Reset and a crop.
    onPictureChange: change,
    onPictureDelete(id) {
      if (readOnly) return;
      if (selRef.current?.id === id) setSel(null);
      setBlocks((prev) => (findBlock(prev, id) ? removeBlockTree(prev, id) : prev));
    },
    // A copy of picture `id` beside it, fn(its picture) placing it: its
    // caption and place only (not the notes under it), over it, selected.
    onPictureDuplicate(id, fn) {
      const src = findBlock(blocksRef.current, id);
      const pic = normalizePicture(src?.properties?.picture);
      if (readOnly || !pic) return;
      const copy = makeBlockId();
      const { pdf_page } = src.properties;
      const properties = { t0: wallNow(), picture: fn(pic), ...(pdf_page !== undefined ? { pdf_page } : {}) };
      setBlocks((prev) => insertSibling(prev, id, { id: copy, parentId: src.parentId ?? null, children: [],
        content: src.content || "", properties }, true));
      setSel({ id: copy, cropping: false });
    },
    // Bring to front ("front") or Send to back ("back") among the
    // surface's pictures (orderPicture).
    onPictureOrder(id, where) {
      if (!readOnly) setBlocks((prev) => orderPicture(prev, id, where));
    },
    // Copy image: a PNG of the picture as it shows, made in the click.
    onPictureCopy(id) {
      const pic = normalizePicture(findBlock(blocksRef.current, id)?.properties?.picture);
      if (!pic) return;
      copyPng(shownPng(assetUrl(pic.url), pic)).then((ok) => setStatus(ok ? t("Copied image") : t("Could not copy the image")));
    },
    // Image files dropped on `surface` at `at` (points; PictureLayer).
    onPictureDrop(surface, files, at, size) { place(files, surface, size, at); },
  };

  return {
    sel, flash, bySurface, actions,
    // The strip's Picture button (and its key): the file picker, its files
    // placed on the surface in view as it is when they come.
    pick() {
      if (readOnly) return;
      if (!pasteTarget()) { setStatus(t("A picture goes on a PDF page or a notebook page: open one first.")); return; }
      pickFiles({ accept: "image/*", multiple: true }, (files) => {
        const target = live.current.pasteTarget();
        if (target) place([...files], target.page, target.size, null);
      });
    },
    flashPicture(id) { setFlash({ id, nonce: Date.now() }); },
    // A box or the lasso's strokes were selected (App): the picture lets go.
    deselect() {
      turnRef.current++;
      actions.onPictureDeselect();
    },
  };
}
