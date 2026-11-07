// The documentation sidebar starts folded on a narrow screen (it is written
// open so it shows without this script). Not deferred, and right after the
// sidebar in the page, so it folds it before the first paint.
if (matchMedia('(max-width: 900px)').matches) {
  document.querySelectorAll('details.docnav').forEach(d => { d.open = false; });
}
