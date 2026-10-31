"""Render the README stories recorded on take.mjs (the two AI demos, collaboration and the
Notability import with ink) from their kept takes in artifacts/readme-media/<name>."""
import argparse

from compose import Camera, FULL, Take, VH, VW, hold, publish_demo
from media_output import ROOT

SCRATCH = ROOT / 'artifacts/readme-media'


def native_agentic():
    """record-native-agentic.mjs: a boxed figure asked about, its cited
    passage followed; then a highlighted clause's refs 3-5 searched, read and
    compared in a table that cites the reference list."""
    take = Take(SCRATCH / 'native-agentic')   # refuses a take whose checks failed
    m, f = take.marks, take.framing
    # The whole window (recorded at a 150% interface size, so the chat reads
    # at README width) from the first frame to the last, but for one eased
    # push-in: as the citation's click jumps the PDF, the camera moves in on
    # P, the PDF pane whole (its own edges, so no word is cut) from its top,
    # where the viewer puts the marked passage 80 px down, at about 1.9x: the
    # caption's words and their mark read at README width. Part 2 is a new
    # shot after a hard cut, on the whole window again, so the loop blends
    # one framing. The push starts with the jump itself (the passage is
    # marked a frame after the PDF moved): the camera follows the jump
    # instead of re-framing a settled page, and a moving frame costs the WebP
    # a whole frame.
    pane = f['pdf']
    w = min(pane['x'] + pane['width'] + 2, f['chat']['x'] - 1) - pane['x']
    P = (pane['x'], pane['y'], w, w * 900 / 1440)
    box = f['passage']
    assert P[1] + 40 <= box['y'] and box['x'] + box['width'] <= P[0] + P[2] and box['y'] + box['height'] <= P[1] + P[3], (P, box)
    push = m['marked'] - 0.08
    assert push + 0.5 <= m['toPassage'] + 0.02, (push, m)
    camera = Camera(FULL).move(push, P, 0.45).move(m['part2'] - 0.16, FULL, 0)

    # The research pill: each tool's label from the first frame it rests
    # whole to the last before it changes, held still to about 1 s when the
    # real call was quicker (only the pill's shimmer would move).
    def label(shown, gone, seconds=1.0, cut=0):
        last = gone - 0.04
        plays = (shown, last, 1, cut)
        return [plays, hold(last, seconds - (last - shown))] if last - shown < seconds else [(shown, shown + seconds, 1, cut)]

    segments = [
        # The paper's Fig. 1 and the empty chat, held so the eye finds the scene.
        (m['start'] + 0.05, m['glide1'] - 0.15),
        # The pointer onto panel c's corner, the "Ctrl" keycap, the Ctrl+drag
        # over c and d; the figure lands in the composer and the area's
        # colour toolbar settles by the box, the keycap faded out (pointer
        # travel at 1.2x).
        # The screencast shows the thumbnail about 0.2 s after the page has
        # it, so the segment runs on to the pointer's start towards the box.
        (m['glide1'] - 0.15, m['toInput'] - 0.03, 1.2),
        # Cut: the glide into the message box. The question types at 1x;
        # Enter sends it and it rises into the chat; the first-token wait
        # plays on at 2.5x (only the Thinking pill turns), then the answer
        # streams at 2x, the pill's morph to "Responding" passing in it.
        (m['type1'] - 0.05, m['send1'] + 0.35),
        (m['send1'] + 0.35, m['a1Start'], 2.5),
        (m['a1Start'], m['a1Done'], 2),
        # A short rest (2x), the pointer onto the p. 2 chip (1.2x); the quote
        # preview holds; the click jumps the PDF from the figure to its
        # caption, which was below the view, and the camera pushes in on the
        # marked passage; the pointer goes onto the mark (1.2x) and rests.
        (m['a1Done'], m['toChip'], 2),
        (m['toChip'], m['preview'], 1.2),
        (m['preview'], m['toPassage']),
        (m['toPassage'], m['pointed'], 1.2),
        (m['pointed'], m['pointed'] + 0.85),
        # A hard cut back to the whole window, on page 1 (the PDF's move there is
        # left out): the abstract with its yellow clause; the pointer onto it
        # and a plain click (1.2x); the "PDF passage" chip holds a moment.
        (m['part2'] - 0.15, m['chip'], 1.2, 0),
        (m['chip'], m['chip'] + 0.55),
        # Cut: the glide into the box. The second question at 1x; Enter; it
        # rises into the chat; "Thinking" plays on at 3x until the pill rests
        # on "Searching papers, 3 queries…".
        (m['type2'] - 0.05, m['send2'] + 0.45),
        (m['send2'] + 0.45, m['searchingShown'], 3),
        *label(m['searchingShown'], m['searchingGone'], 0.95),
        # Hard cuts (only the pill's words change; a dissolve would ghost one
        # label through the other): "Fetching 3 documents…", then the table
        # from the first frame the pill rests on "Responding", so neither
        # the "Thinking" between rounds nor a half-changed label shows.
        *label(m['fetchingShown'], m['fetchingGone'], 0.85, cut=0),
        (m['a2Resp'], m['a2Done'], 2, 0),
        # A hard cut over the pill's last morph ("Responding" to the steps
        # line): the finished answer, from "6 steps · searched online 3
        # times, fetched 3 documents" to "Save to library", held before the
        # loop dissolves back to the first frame.
        (m['a2Settled'] + 0.04, m['a2Settled'] + 1.64, 1, 0),
    ]
    # The second question's bubble shows its passage as ChatDock's plain
    # text ("> " and the PDF's line break), as the app does.
    return publish_demo('native-agentic', take.capture, segments, camera, take.dir, loop_fade=0.2, motion_quality=25)


def agentic_notes():
    """record-agentic-notes.mjs: a slide pasted into the chat lands in the
    note as display math under the caret's block, after Allow once."""
    take = Take(SCRATCH / 'agentic-notes')   # refuses a take whose checks failed
    m = take.marks
    segments = [
        # The note, its caret on the caption, the chat beside it: held still
        # so the eye finds the scene.
        (m['glide'] - 0.7, m['glide']),
        # Into the chat box and the click, the pointer stepping off to the
        # blank space above the composer (clear of the "Block at your
        # cursor" chip), Ctrl+V and the slide's thumbnail: pointer travel at
        # 1.2x.
        (m['glide'], m['typeStart'], 1.2),
        # The request types at 1x, whole: it is under two seconds, and a cut
        # inside it dissolves across the composer growing a line.
        (m['typeStart'], m['typed']),
        # The typed request rests a moment before Enter: only the caret
        # blinks, so 1.5x does not show.
        (m['typed'], m['sent'], 1.5),
        # Enter: the slide and the request rise into the chat at 1x...
        (m['sent'], m['asked'] + 0.45),
        # ...and read in the bubble during "Thinking" (the scripted model
        # takes about a second), at 1.25x: only the pill's shimmer moves.
        # Not cut: a dissolve here would only blur the card's arrival.
        (m['asked'] + 0.45, m['card'], 1.25),
        # The card with the LaTeX comes up at 1x, then is read, still, for
        # about 0.7 s more at 1.25x (nothing moves on it).
        (m['card'], m['card'] + 0.35),
        (m['card'] + 0.35, m['toAllow'], 1.25),
        # Onto Allow once and the click, then the equation lands rendered
        # under the caption while the pointer moves off the card and the
        # status pill changes from "Waiting for your approval" towards
        # "Responding": pointer travel at 1.2x, which also hurries the
        # pill's label morph through.
        (m['toAllow'], m['replyStart'], 1.2),
        # The one-line reply streams at 2x; the 2x runs on 0.4 s past its
        # end so the pill's morph from "Responding" to "1 step" passes in
        # sped-up time instead of showing a half-changed word.
        (m['replyStart'], m['replyDone'] + 0.4, 2),
        # The finished state holds before the loop.
        (m['replyDone'] + 0.4, min(m['end'], m['replyDone'] + 1.9)),
    ]
    # The note and the chat sit at opposite edges of the window: one framing,
    # the whole window, at the recorded 130% interface size.
    return publish_demo('agentic-notes', take.capture, segments, Camera(FULL), take.dir)


def collab():
    """record-collab.mjs: Maya and Sam type into the shared page while Alex
    clicks into the empty block, pastes a screenshot of Fig. 1c, d, clicks
    out under it and drags its right grip out to size it."""
    take = Take(SCRATCH / 'collab')   # refuses a take whose checks failed
    m, f = take.marks, take.framing
    # Real time throughout: three people working at once is the story, and
    # their typing has to look like people typing (Alex's pointer moves while
    # they type, so it cannot be sped up alone). One cut: Alex's trip to the
    # blank page under the notes to close the block's editor (Gamma's Escape
    # does not), a click that would read as a stray one. The recorder pauses
    # Maya across it and starts Sam after it, so the dissolve drops no key.
    assert m['mayaPause'] <= m['leaveGo'] - 0.3 and m['mayaResumes'] >= m['toGrip'] + 0.3 and m['samTypes'] >= m['toGrip'] + 0.3, m
    segments = [
        # The page with Maya's and Sam's carets and flags and both avatars
        # in the page head, held still a moment before anything moves.
        hold(m['start'], 0.25),
        (m['start'], m['reach']),
        # Maya starts her question; Alex clicks into the empty block, the
        # "Ctrl V" keycap shows by the pointer and the screenshot lands; it
        # holds while Maya stops to think.
        (m['reach'], m['leaveGo']),
        # (cut: the click out of the block)
        # Alex's pointer goes onto the picture's right grip and drags it out
        # from 400 to 600 px, then flicks off it at once (on release the
        # picture re-renders and its toolbar goes with the pointer); Maya
        # finishes her question and Sam adds to the takeaway. Everyone's words
        # and the sized picture hold the recorded 1.8 s, then the last frame
        # (nothing on the page moves) a little longer before the loop.
        (m['toGrip'], m['end']),
        hold(m['end'], 0.5),
    ]
    # One still framing: Alex's window is recorded narrow (1040 CSS px, the
    # card's 16:10), so its whole width below the tab strip holds the page
    # head with the presence avatars, the peers' lines and flags and the
    # picture as pasted and as sized, at about 1.4x.
    window = take.timing['window']
    w = window['width']
    h = w * 900 / 1440
    top = f['title']['y'] - 2
    bottom = max(f['page']['y'] + f['page']['height'], f['picture']['y'] + f['picture']['height']) + 4
    assert bottom - top <= h and f['presence']['x'] + f['presence']['width'] <= w, (top, bottom, h)
    camera = Camera((0, top, w, h))
    # The camera never moves, so the frames are encoded at a higher quality:
    # at the default one the pale-blue row keeps faint lines where the keycap
    # and the pasted picture's edges were, and the clip stays small anyway.
    return publish_demo('collab', take.capture, segments, camera, take.dir, quality=90)


def _settles(capture, after, reference, box, tolerance=0.6):
    """The first capture time >= `after` whose CSS `box` matches the frame at
    capture time `reference` (mean luminance difference under `tolerance`):
    when a closing modal and its dimmed backdrop have fully gone."""
    from PIL import Image, ImageChops, ImageStat
    s = capture.scale
    crop = tuple(round(v * s) for v in (box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height']))

    def grey(i):
        with Image.open(capture.dir / capture.files[i]) as im:
            return im.convert('L').crop(crop).reduce(4)
    target = grey(capture.index(reference))
    for i, t in enumerate(capture.times):
        if t >= after and ImageStat.Stat(ImageChops.difference(grey(i), target)).mean[0] < tolerance:
            return t
    raise ValueError(f'The dialog never cleared after {after}')


def ink_import():
    take = Take(SCRATCH / 'ink-import')
    m, f, cap = take.marks, take.framing, take.capture
    # The library after Done, once the dialog and its backdrop have gone: the
    # dialog's own area matches the library as it is just before the double
    # click (the pointer resting on the new row, its hover band shown). The
    # pointer reached the row while the dialog closed, off camera.
    clear = _settles(cap, m['rowArrived'], m['openRow'] - 0.05, f['report'])
    library = max(clear, m['openRow'] - 0.42)
    # The close-up: the paper alone, from the window's left edge (the tool
    # rail) to just past the margin bracket, so the metadata column is whole;
    # its top below the paper's title, so no title words are cut, and well
    # under the open pen toolbar and its shadow. The Notes column stays out of
    # it (its ink cards are captioned "(empty)", the app's placeholder for a
    # block with no text, and would be the largest words in a close-up); the
    # new card shows in the final hold on the whole window.
    right = max(f['oldInk']['x'] + f['oldInk']['width'], f['newInk']['x'] + f['newInk']['width']) + 36
    w = max(right, VW / 1.6)
    h = w * VH / VW
    title_bottom = f['title']['y'] + f['title']['height']
    top = min(VH - h, max(title_bottom + 14, f['oldInk']['y'] - 120))
    assert top >= title_bottom + 4, 'the close-up would cut the title'
    assert top >= f['inkBar']['y'] + f['inkBar']['height'] + 10, 'the close-up would show the pen toolbar'
    assert top + h >= f['oldInk']['y'] + f['oldInk']['height'] + 16, 'the close-up would cut the ink'
    paper = (0, top, w, h)
    move_in = m['rested'] + 0.05
    move_out = m['parked'] + 0.55
    assert move_in + 0.45 < m['draw'], 'the camera must settle before the first stroke'
    page_open = max(m['pdfText'] + 0.15, m['nearTools'] + 0.05)
    assert m['end'] - move_out - 0.5 >= 1.5, 'the final hold is too short'
    segments = [
        # The library home, held so the eye finds it, and the same take
        # running on: no dissolve between two pointer positions.
        hold(m['menu'], 0.6),
        # ≡ View → Import…, the Import dialog (its logos loaded off camera
        # before the take), the Notability card and its instructions, Choose
        # files… pressed, all at 1.3×. The file picker is answered by setFiles
        # and never shows.
        (m['menu'], m['choose'] + 0.08, 1.3),
        # The upload is cut hard (the dialog swaps in place, and a dissolve
        # would ghost the card grid through the review). The review, once it
        # lists the note under "Library after import" (kind PDF; the probe
        # sees the DOM about 0.1 s before the screencast shows it), held, then
        # the pointer straight on to Import to library (round Cancel), the
        # press, the "Working…" card.
        (m['review'] + 0.15, m['review'] + 0.6, 0, 0),
        (m['review'] + 0.15, m['working'] + 0.3, 1.3),
        # The import job's progress is cut hard: "Import complete" (1 new
        # page · 21 strokes), held, then Done pressed; the edit leaves as the
        # button is released, before the dialog starts to fade.
        (m['completeReady'] + 0.05, m['done'] + 0.04, 1.3, 0),
        # A hard cut to the library with the new page on top, the pointer
        # resting on it (its hover band marks the row), then the double click.
        (library, m['openRow'] + 0.1, 1.0, 0),
        # A hard cut to the page once its PDF and ink have painted (the blank
        # page, the "Rendering page" pill and the layout settling are cut):
        # the pencil marks on the paper and their ink cards in Notes, 0.9 s of
        # output for the still page (the pointer moved beside the Handwriting
        # tools button while the page opened), running on into the next beat.
        (page_open, m['tools'], max(1.0, (m['tools'] - page_open) / 0.9), 0),
        # Handwriting tools → Blue pen, and a paced glide down to a rest just
        # below the phrase, all at 1.3×.
        (m['tools'], m['rested'], 1.3),
        # The pointer rests there while the camera eases in on the paper.
        (m['rested'], m['draw'], 1.0),
        # The ring and the arrow to "how?" drawn at 1.5×; the new ink card
        # appears in Notes (off this framing). The hand tool (V) is picked, so
        # the pen's hover ring goes, and with no cut the pointer moves into
        # the paper's empty left margin at 1.3×.
        (m['draw'], m['away'], 1.5),
        (m['away'], m['parked'], 1.3),
        # The close-up holds 0.55 s, the camera eases back to the whole window
        # (the opening framing, so the loop dissolves within one framing), and
        # the final state holds about 1.6 s: the paper with both hands' ink,
        # and the three ink cards in Notes.
        (m['parked'], m['end'], 1.0),
    ]
    camera = Camera().move(move_in, paper, 0.45).move(move_out, FULL, 0.45)
    # The two camera moves and the loop's dissolve are most of the file: the
    # moving frames are encoded a little lower than the default (at 25 the
    # ease-out left a faint step pattern in the paper's white margin), and
    # the loop dissolves in 0.4 s.
    return publish_demo('ink-import', cap, segments, camera, take.dir, loop_fade=0.4, motion_quality=34)


CASES = {'native-agentic': native_agentic, 'agentic-notes': agentic_notes,
         'collab': collab, 'ink-import': ink_import}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='+', choices=[*CASES, 'all'])
    args = parser.parse_args()
    for name in CASES if 'all' in args.cases else args.cases:
        CASES[name]()
