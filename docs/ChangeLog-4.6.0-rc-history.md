# SuperPicky 4.6.0 — RC history / RC 阶段增量记录

本文件归档 4.6.0 开发期各 RC 版本的**增量**说明（RC1 → RC2），仅供追溯。
面向用户的完整发布说明见仓库根目录 `ChangeLog.md`，其中已并入下列全部内容。

This file archives the per-RC **incremental** notes from the 4.6.0 development
cycle (RC1 → RC2) for traceability only. The user-facing release notes live in
`ChangeLog.md` at the repository root, which already incorporates everything
below.

---

# SuperPicky 4.6.0 RC2

**What's new since RC1:**

- **Fix a whole misidentified species in one go.** When a batch gets the same
  bird wrong from end to end, right-click any of those photos and pick
  `Change all <species> to…`. It retags every photo of that species in the database —
  not just the ones currently filtered on screen — and moves them into the new
  species' folders, keeping burst groups together. Before anything moves you get
  a confirmation showing how many photos are involved, how many burst groups,
  and the exact target folders, so a batch organised in English won't quietly
  grow a second set of folders in Chinese. When it finishes you are told what
  moved, what was only retagged because it had never been organised, and
  anything that failed and why. Related fix: changing a species used to fail
  silently when a file with the same name already sat in the target folder — the
  database was updated while the file stayed put. Now the file and the database
  never disagree, and you are told about the collision.

- **"Picked only" is now its own switch, and your picks always sort first.**
  The crown used to sit in the row of star filters, where it looked like it
  added photos to the list — it actually cut the list down to just your picks.
  It is now a separate checkbox under that row. And because a pick is the
  overlap of the sharpest and the best-looking of your 3-star shots, sorting by
  sharpness or rarity alone used to scatter them: in one test the twelve picks
  landed at positions 2, 4, 8 … 44, and as far down as 120 when sorted by
  rarity. Picks now always come first, with your chosen sort applied inside
  them. Sorting by filename is left alone, since its whole point is shooting
  order.

- **Anonymous usage statistics — and a switch to turn them off.** Settings →
  About now has a switch for anonymous usage statistics, and the first launch
  tells you what is collected before anything is sent. What is sent: the app
  version, your operating system, the interface language, and a random ID that
  changes every day. What is never sent: photos, file paths, or personal
  information. (The previous statistics endpoint had quietly stopped working
  months ago, which is why this was rebuilt.)

- **Check for a newer version from the About page.** The About page has its
  website link back, plus a button that looks up the current release when you
  ask it to. Nothing is checked in the background and nothing is downloaded or
  installed — it only reads the version number when you click.

- **Dark menus no longer show white edges.** Drop-down lists throughout the app
  — filters, sorting, the bird ID country and region pickers, Settings — were
  drawn on top of the macOS light panel, leaving white strips above and below
  the list. Right-click menus in text fields carried icons drawn for a light
  theme, which were all but invisible on a dark menu.

- **The app no longer hangs forever when an external tool stops responding.**
  Thirteen places that call out to external programs had no time limit, so one
  stuck call could freeze the app for good.

- **Folders processed by older versions open again.** A results database
  written by an earlier version could be missing columns the browser expects;
  missing columns are now filled in on open.

- **What the app tells you now matches what it does.** The star rules on the
  console and in step 2 of the usage guide describe the batch-quota system
  actually in use, the burst note quotes the minimum you configured instead of
  a hard-coded 4, and a few Chinese strings that leaked into the English
  interface are gone.

- **Smaller fixes.** On macOS the app no longer leaves behind the helper that
  keeps your Mac awake after you quit; deleting files copes with unusual
  characters in filenames; the aesthetics threshold can go as low as the slider
  allows instead of snapping back; and cancelling an Apple Photos import now
  actually stops.

---

# SuperPicky 4.6.0 RC1

**What's new since 4.5.0:**

- **Send your keepers straight to Apple Photos (macOS only).** The results
  browser has a new "Add to Photos" button. It imports the RAW file whenever one
  exists — on a 985-image test shoot every single imported item was the NEF, not
  the paired JPEG — and writes the bird's name, your star rating and the quality
  figures into the Photos title, description and keywords, so you can search for
  a species inside Photos itself. Photos you have already sent across are
  skipped, so running it a second time will not duplicate anything. If you have
  ticked any thumbnails, only those are imported; if you have ticked none, the
  whole filtered list goes. Each run creates or reuses an album named after the
  folder and the date, filed under a "SuperPicky Imports" folder. Your RAW files
  are never modified, and XMP sidecars are never sent to Photos. Contributed by
  @orientaldollarbird.
