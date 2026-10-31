// Every guide by id: the tours, in the order the Tours menu lists them, then
// the setup guides (listed under their own heading), then the hints. Add a
// file per tour and a line here.
// The order runs from getting something into the library, through working on
// a page, to keeping the library itself in order. A triggered tour listed
// earlier wins an offer two tours could take (Arrange windows before the
// PDF viewer, both offered on opening a paper).
import firstRun from "./firstRun.js";
import addPaper from "./addPaper.js";
import aiChat from "./aiChat.js";
import notebook from "./notebook.js";
import windows from "./windows.js";
import { pdfViewer, notebookView } from "./viewer.js";
import highlights from "./highlights.js";
import citations from "./citations.js";
import sharing from "./sharing.js";
import tables from "./tables.js";
import notes from "./notes.js";
import handwriting from "./handwriting.js";
import library from "./library.js";
import tasks from "./tasks.js";
import presence from "./presence.js";
import workspaces from "./workspaces.js";
import recording from "./recording.js";
import search from "./search.js";
import comments from "./comments.js";
import zoteroLink from "./zoteroLink.js";
import assistants from "./assistants.js";
import notability from "./notability.js";
import texFollow from "./texFollow.js";
import hints from "./hints.js";

export const TOURS = Object.fromEntries(
  [firstRun, addPaper, aiChat, notebook, windows, pdfViewer, notebookView, highlights, citations, search, sharing, comments, tables,
    notes, handwriting, recording, library, tasks, presence, workspaces, zoteroLink, notability, assistants, texFollow, ...hints]
    .map((t) => [t.id, t]));
