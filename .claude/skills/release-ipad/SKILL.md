---
name: release-ipad
description: Ship the iPad app to TestFlight between desktop releases by merging a commit whose message carries a fresh `ipad-test-release-NNN` marker into main; the Mac mini picks it up, builds, tests, signs and uploads on its own. Every desktop release (the `release` skill) already sends one. TestFlight only, never an App Store submission.
---

# Release the iPad app to TestFlight

The iPad app is not built for release by GitHub Actions (`ipad.yml` only
tests and makes an unsigned build). Every desktop release from `main` (the
`release` skill) pushes a marker for it at the end; this skill is for a
TestFlight build without a desktop release, or after a release whose
marker step warned that `main` had moved. A Mac mini polls `tim4431/gamma`'s `main` every minute for a commit whose message
(subject or body) contains a trigger marker, and for a new one it builds
the app, runs the tests, signs and uploads the build to TestFlight. Nothing
is dispatched by hand, and nothing is submitted to the App Store.

The marker is `ipad-test-release-NNN`, a three-digit serial that only
identifies the trigger — it is not the app's version. A marker that was
already used is ignored, so every release takes a new number, and a retry
after a failed build takes a new number too.

## Steps

1. **Pick the next number**: the highest marker already on `main`, plus one:

   ```bash
   git fetch -q origin
   git log origin/main --format=%B | grep -oE 'ipad-test-release-[0-9]{3}' | sort -u | tail -1
   ```

   Nothing found → `001`.

2. **Put the marker in a commit that will reach `main`.** The commit must
   be on the branch about to merge: usually a doc or release-note commit on
   `dev` whose subject or body ends with the marker, e.g.
   `... (ipad-test-release-002)`. This is the one place a release skill
   commits, and only with the user's go-ahead for the release itself.

3. **Merge with the `merge` skill**, with a merge commit (`gh pr merge
   --merge`, the skill's default), which keeps the marked commit's message
   on `main`. A squash merge rewrites the message: then the marker has to
   be in the squashed commit's title or body, or the Mac mini never sees it.

4. **Report**: the marker number and that the Mac mini will pick it up
   within a minute. Its progress is not visible from here: the build
   appears in App Store Connect's TestFlight tab when it is done. A
   failure is fixed on the branch as normal work, then this skill again
   with the next number.

Details: `docs/dev/github_actions.md`'s iPad section, `docs/dev/ipad.md`.
