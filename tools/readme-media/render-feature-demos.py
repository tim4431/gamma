"""Render the annotation/ink and the two AI README stories from retina captures."""
import argparse
import json

from compose import Capture, Camera, FULL, focus, publish_demo
from media_output import ROOT

SCRATCH = ROOT / 'artifacts/readme-media'


def annotate_and_ink():
    directory = SCRATCH / 'annotate-and-ink'
    timeline = json.loads((directory / 'ink-timeline.json').read_text(encoding='utf-8'))
    m, f = timeline['marks'], timeline['framing']
    if not timeline['verified'].get('annotation'):
        raise ValueError('Capture must include a persisted text annotation')
    # Close in on the sentence, its colour popup and the note being typed; the
    # pen, the lasso edit and the ink card use the whole window.
    camera = (Camera()
              .move(m['start'] + 0.3, focus(f['sentence'], f['tip'], f['note'], f['notesHead'], margin=30), 0.5)
              .move(m['annotation'] + 0.05, FULL, 0.5))
    return publish_demo('annotate-and-ink', Capture(timeline['frames']), [(m['start'], m['end'])], camera, directory)


def native_agentic():
    directory = SCRATCH / 'revised'
    timeline = json.loads((directory / 'agentic-timeline.json').read_text(encoding='utf-8'))
    m, f, verified = timeline['marks'], timeline['framing'], timeline.get('verified', {})
    if not all(verified.get(key) for key in ('boxAttachment', 'savedFigure', 'citationMarks', 'singleConversation', 'pdfChat')) or verified.get('savedPapers', 0) < 2:
        raise ValueError('Capture the figure question, an exact citation and two saved papers first')
    if verified.get('expandedSteps') != 0 or verified.get('citationNotice'):
        raise ValueError('Keep tool steps collapsed and show an exact PDF citation match')
    # Real speed throughout; only the model's waits are cut, each with a
    # dissolve inside one framing (a dissolve between framings changes every
    # pixel and costs as much as a camera move). The figure, the question and
    # the cited passage use the whole window; the camera then closes in on the
    # agent's steps, its approval card and what it saved, and stays there.
    ask = (m['start'], m['pdfSent'] + 0.8)
    read = (max(ask[1], m['pdfAnswer'] - 3), m['saveSent'] + 0.8)
    approve = (max(read[1], m['approval'] - 1.5), m['allowed'] + 1.2)
    saved = (max(approve[1], m['saveAnswer'] - 3), m['end'])
    camera = Camera().move(m['saveSent'] + 0.1, focus(f['approval'], f['saveAnswer'], margin=30), 0.5)
    return publish_demo('native-agentic', Capture(timeline['frames']), [ask, read, approve, saved], camera, directory, loop_fade=0.3)


def agentic_notes():
    directory = SCRATCH / 'agentic-notes'
    timeline = json.loads((directory / 'agentic-notes-timeline.json').read_text(encoding='utf-8'))
    m, verified = timeline['marks'], timeline.get('verified', {})
    if not verified.get('katex'):
        raise ValueError('Capture the pasted equation saved as a KaTeX block first')
    # The note and the chat sit at opposite edges: the whole window, recorded
    # at a 130% interface size. The model's wait is cut.
    ask = (m['start'], m['sent'] + 1.0)
    rest = (max(ask[1], m['approval'] - 1.0), m['end'])
    return publish_demo('agentic-notes', Capture(timeline['frames']), [ask, rest], Camera(), directory)


CASES = {'annotate-and-ink': annotate_and_ink, 'native-agentic': native_agentic, 'agentic-notes': agentic_notes}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='+', choices=[*CASES, 'all'])
    args = parser.parse_args()
    for name in CASES if 'all' in args.cases else args.cases:
        CASES[name]()
