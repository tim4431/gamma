// Every guide by id: the tours, in the order the Tours menu lists them, then
// the hints. Add a file per tour and a line here.
// The order runs from getting something into the library, through working on
// a page, to keeping the library itself in order.
import firstRun from "./firstRun.js";
import addPaper from "./addPaper.js";
import aiChat from "./aiChat.js";
import notebook from "./notebook.js";
import windows from "./windows.js";
import citations from "./citations.js";
import sharing from "./sharing.js";
import tables from "./tables.js";
import handwriting from "./handwriting.js";
import library from "./library.js";
import tasks from "./tasks.js";
import presence from "./presence.js";
import workspaces from "./workspaces.js";
import hints from "./hints.js";

export const TOURS = Object.fromEntries(
  [firstRun, addPaper, aiChat, notebook, windows, citations, sharing, tables, handwriting, library, tasks,
    presence, workspaces, ...hints].map((t) => [t.id, t]));
