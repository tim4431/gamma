// The topbar items' icons (app/topbarItems.js): their rows in Settings →
// Appearance › Top bar, and the buttons of a pane whose page is a view
// while pages are side by side (App's paneStandIns), which stand for the
// live pane's own until a press makes their pane live.
import { ActivityIcon, BellIcon, CloudIcon, LinkIcon, MenuIcon, PlusIcon, SearchIcon } from "../shared/ui/Icons";

export const TOPBAR_ICONS = Object.freeze({
  add: PlusIcon, tasks: ActivityIcon, search: SearchIcon, share: LinkIcon, sync: CloudIcon, inbox: BellIcon, view: MenuIcon,
});
