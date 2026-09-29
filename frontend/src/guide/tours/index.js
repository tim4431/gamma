// Every guide by id: the tours, in the order the Tours menu lists them, then
// the hints. Add a file per tour and a line here.
import firstRun from "./firstRun.js";
import aiChat from "./aiChat.js";
import windows from "./windows.js";
import citations from "./citations.js";
import sharing from "./sharing.js";
import tables from "./tables.js";
import handwriting from "./handwriting.js";
import presence from "./presence.js";
import workspaces from "./workspaces.js";
import hints from "./hints.js";

export const TOURS = Object.fromEntries(
  [firstRun, aiChat, windows, citations, sharing, tables, handwriting, presence, workspaces, ...hints].map((t) => [t.id, t]));
