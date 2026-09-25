// Every guide by id: the tours, in the order the Tours menu lists them, then
// the hints. Add a file per tour and a line here.
import firstRun from "./firstRun.js";
import aiChat from "./aiChat.js";
import citations from "./citations.js";
import sharing from "./sharing.js";
import tables from "./tables.js";
import handwriting from "./handwriting.js";
import presence from "./presence.js";
import conflicts from "./conflicts.js";
import workspaces from "./workspaces.js";
import hints from "./hints.js";

export const TOURS = Object.fromEntries(
  [firstRun, aiChat, citations, sharing, tables, handwriting, presence, conflicts, workspaces, ...hints].map((t) => [t.id, t]));
