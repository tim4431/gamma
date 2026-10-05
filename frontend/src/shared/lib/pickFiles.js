// The system file picker, opened from code. iOS Safari may not open it for
// an <input> outside the document, or may drop that input before its
// `change`; so the input joins the page, hidden, for the pick and leaves
// after it. `onPick(files)` runs when something was picked.
export function pickFiles({ accept = "", multiple = false } = {}, onPick) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = accept;
  input.multiple = multiple;
  input.hidden = true;
  input.addEventListener("change", () => {
    input.remove();
    if (input.files?.length) onPick(input.files);
  }, { once: true });
  input.addEventListener("cancel", () => input.remove(), { once: true });
  document.body.append(input);
  input.click();
}
