# Chrome's tab groups, as Chromium draws them

How Chrome draws and works a tab group in its tab strip, read from
Chromium's source (main, November 2026), and what Gamma's strip groups took
from it ([dev/strip_groups.md](../dev/strip_groups.md) has the mechanics as
built). The first strip groups had the parts but not the look: a tinted
chip, a line under each tab with gaps between them, and an outline on the
active tab that met nothing. Measured against Chrome, they were redrawn.

## Sources

Under `chrome/browser/ui/views/tabs/`: `tab_group_header.cc` (the chip and
its input), `tab_group_underline.cc` / `.h`, `tab_group_views.cc`,
`tab_group_style_views.cc`, and in `common/` `tab_group_style.cc` / `.h`
and `horizontal_tab_style_views.cc` (a tab's outline). Also
`chrome/browser/ui/tabs/tab_style.cc`, `chrome/browser/ui/layout_constants.cc`,
`chrome/browser/ui/tabs/tab_group_theme.cc` and the colour table in
`chrome/browser/ui/color/chrome_color_mixer.cc` (`TabGroupColorParams`),
with the hex of the Google palette from `ui/gfx/color_palette.h`.

## The measures

| Part | Chrome | Source |
|---|---|---|
| Strip | 41 dp: a 35 dp tab (34 + 1 overlapping the toolbar) under 6 dp padding | `kTabStripHeight`, `kTabHeight`, `kTabStripPadding`, `kTabstripToolbarOverlap = 1` |
| Chip (header) | 20 dp tall, corners 6 dp, padding 2 dp top and bottom and 6 dp each side, centred in the tab's height; filled with the group's colour | `kEmptyChipSize = 20`, `kCornerRadius = 6`, `GetInsetsForHeaderChip() = VH(2, 6)`, `GetTitleChipOffset` |
| Unnamed chip | a 20 × 20 square of the colour, the same corners | `empty_width = chip_height`, `GetEmptyTitleChipBackground` |
| Chip's name | black or white, whichever has the more contrast with the colour (white below a relative luminance of about 0.211, else Google Grey 900); line height 20; capped at half a standard tab, about 119 dp; the tail fades out | `GetColorWithMaxContrast(color_)`, `text_max_width`, `FADE_TAIL` |
| Header's room | 16 dp either side of the chip, which the tabs' 18 dp overlap eats into | `GetLeadingGroupHeaderPadding`, `GetTabGroupViewOverlap` |
| Underline | 2 dp, its ends fully round, 1 dp above the strip's foot (where the active tab meets the toolbar); from the header's start to the last tab's end, each inset 18 dp, so it starts under the chip; hidden while the group is collapsed (the header is then the whole group) | `kStrokeThickness = kDefaultHaloThickness = 2`, `GetUnderlinePath` (`MakeRectXY(rect, 1, 1)`), `GetUnderlineStrokeInset`, `TabGroupUnderlineShouldBeHidden` |
| Active tab in a group | its outline drawn in the group's colour, 2 dp, the underline's thickness; the underline reaches 2 dp past it ("the rounded bits poking out the sides") | `GetStrokeThickness`, `PaintBackgroundStroke(group_color)`, the active tab's `-kStrokeThickness` insets |
| Tab shape | top corners up to 10 dp; the bottom corners flare outward with 12 dp arcs into the strip's foot, which is how the outline meets the underline | `GetTopCornerRadius`, `GetBottomCornerRadius() = 12` |
| Other tabs in the group | no outline, hover only fills | `GetStrokeThickness` returns 0 |
| Separators | 2 × 16 dp, rounded; the header draws its own after a collapsed group | `kSeparatorThickness`, `kSeparatorHeight`, `GetSeparatorOpacities` |

## The colours

Chrome's classic palette (the refresh behind `IsTabGroupColorRefreshEnabled`
has values this reading did not find). A group's colour depends on whether
the frame is dark or light (`SelectBasedOnDarkInput`):

| Colour | Dark frame | Light frame |
|---|---|---|
| Grey | Grey 300 `#DADCE0` | Grey 700 `#5F6368` |
| Blue | Blue 300 `#8AB4F8` | Blue 600 `#1A73E8` |
| Red | Red 300 `#F28B82` | Red 600 `#D93025` |
| Yellow | Yellow 300 `#FDD663` | Yellow 600 `#F9AB00` |
| Green | Green 300 `#81C995` | Green 700 `#188038` |
| Pink | Pink 300 `#FF8BCB` | Pink 700 `#D01884` |
| Purple | Purple 300 `#C58AF9` | Purple 500 `#A142F4` |
| Cyan | Cyan 300 `#78D9EC` | Cyan 900 `#007B83` |
| Orange | Orange 300 `#FCAD70` | Orange 400 `#FA903E` |

By the luminance rule the name is Grey 900 on every dark-frame colour and
white on the light frame's, but on yellow and orange, which are light.

## Behaviour

- A left click (a tap, Enter or Space) on the header collapses or expands
  the group; a right click (a long press) opens the editor bubble under
  it, anchored at its top left. A press starts a drag of the whole group.
- Ctrl+← / → (⌘ on a Mac) on the focused header moves the group past its
  neighbour.
- While collapsing, the group's tabs shrink until each reaches its minimum
  width and is hidden; the underline goes with the last.
- The header has a focus ring of its own, rounded as the chip, and a hover
  card; its colour does not change on hover.

## What Gamma took, and what it left

Taken: the solid chip with Chrome's height, corners and padding and its
black-or-white name; the unnamed square; the classic palette for each
scheme; one 2 px line with round ends from under the chip to the group's
last tab, at the foot where the active tab meets the page, hidden while
collapsed; the active tab's outline in the colour, its feet flaring into
the line; Ctrl+← / →; the focus ring round the pill.

Adapted: Gamma's tabs do not overlap and have square bottom corners, so the
flare is drawn by the active tab alone (two corner boxes, `--tab-foot`, the
8 px radius of a Gamma tab's top corners rather than Chrome's 12), and the
line reaches over the 2 px gap between tabs instead of being one path. A
pane's strip stands on its head's border with no room below, so there the
line runs along the border and meets the outline square. The name is
ellipsed rather than faded, the strip already fading its own end.

Left: the hover card, the shrinking collapse (the tabs sink out as any
closed tab does), the saved groups and their sync icon, the attention
indicator and focus mode (a focused group, its taller chip and its unfocus
button), all of which belong to features Gamma does not have.
