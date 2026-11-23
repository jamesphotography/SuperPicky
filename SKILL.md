---
name: superpicky
description: Cull bird photos, identify species, and review results.
license: AGPL-3.0
compatibility: Requires a local SuperPicky checkout, its Python environment, and access to the media. Photo and video inference require the relevant model files; check ExifTool for photo workflows.
---

# SuperPicky CLI skill

Use this skill to operate SuperPicky on local media, not to modify the project. Run commands from the SuperPicky checkout with its configured Python environment. If your agent's skill installer requires a matching folder name, install the skill as `superpicky/SKILL.md`; the clone itself can keep its existing name.

## Ask the user about the job, not the settings

For a new culling job, ask only what the user has not already answered. Put the selection and location questions in one message, in the user's language. Ask for the source folder if it is missing. If the user asked only for identification, skip the selection question; if they asked only for rating, skip the location question. An export from existing results does not require asking about either culling setting again.

### 1. How selective should this batch be?

> 这批照片你想多留一些、按平时标准挑，还是只留下最好的？
>
> Would you like to keep more candidates, make a balanced shortlist, or keep only the best shots?

| What the user wants | GUI preset to pass |
| --- | --- |
| Keep more candidates for later review | `--skill-level beginner` (新手) |
| A balanced shortlist, or no preference after being asked | `--skill-level intermediate` (初级 in the Simplified Chinese GUI) |
| Only the strongest photos | `--skill-level master` (大师) |

Treat this as a preference for the batch, not a judgment of the photographer. Use the preset directly, without supplying numeric weights, sharpness/aesthetics thresholds, star quotas, or pick percentages. Do not choose `custom` unless the user asks to use an existing custom preset or explicitly wants advanced tuning.

### 2. Where were these birds photographed?

> 这批照片是在哪儿拍的？告诉我国家和省／州或附近城市就行，不用给坐标。如果是几个地方拍的，哪些文件夹对应哪些地方？
>
> Where were these taken? The country and state, province, or nearby city are enough. If the photos are from different places, which folders go with each location?

When the user asks for culling and species ID (or leaves the choice to you), use `--auto-identify --ebird` for photo culling. Resolve ordinary place names to supported codes yourself: `birdid_cli.py list-countries` lists country codes. Set `--birdid-country CODE` and add `--birdid-region CODE` only if the checkout supports that region. For standalone identification the equivalent flags are `--country` and `--region`. For example, South Australia maps to `AU` and `AU-SA`. If only the country is supported, use it without inventing a regional code. If the location is unknown or withheld, use available photo GPS and no manual location; without a usable location, identify without a geographic restriction. On current checkouts a recognized photo GPS location takes priority over the manual fallback; do not silently disable GPS or impose one region on a mixed-location batch. Use separate runs for folders from different places when the mapping is known. Treat the answer as a **Bird ID filter**, not a request to write location metadata or export an eBird checklist.

### 3. Confirm the run, not every toggle

Leave flight detection, burst detection, exposure checks, and other advanced options at the existing settings unless the user asks for an exception or the job depends on it. Do not ask about them on every batch. Summarize the scope, selection preference, Bird ID location if applicable, and planned writes in one short confirmation instead:

> 我会处理「南澳这次的照片」文件夹，按平时标准挑。照片没有 GPS 时，就用南澳大利亚缩小识鸟范围。只写 XMP 边车和本地处理结果，不移动或删除原片。可以开始吗？

If the preflight finds a checkpoint or existing results, show that state and ask only about the next decision (resume, leave it alone, or an explicitly approved rerun). For a resume, reuse the approved settings rather than repeating the intake or changing the selection criteria mid-run.

## Before touching media

1. Resolve the requested path to an absolute path on this machine. Confirm the operation and its scope: one photo folder, a recursive tree, specific images, or a video file/folder. Inventory the matching files and check free space. Do not expand scope to sibling folders or follow symlinks to unrelated media.
2. Inspect existing XMP sidecars and SuperPicky state: `.superpicky/report.db`, `.superpicky/resume_state.json`, `.superpicky_manifest.json`, and `.birdid_manifest.json` where present. Use `info` for photo results and `batch --dry-run` to list recursive targets. If there is a checkpoint or existing processing state, do not silently start a new pass.
3. Check `python superpicky_cli.py --help` or `python birdid_cli.py --help`, then the intended subcommand's `--help`. For example: `python superpicky_cli.py process --help`. Older checkouts may lack a feature or use different flags. Do not substitute unverified flags, upgrade the checkout, or guess when help disagrees. If setup is missing, consult [Run from source](README.md#run-from-source); check dependencies, ExifTool and model files locally before processing.
4. Summarize the preflight and intake together before making changes. Name the exact targets, mode, writes, and any proposed moves, cleanup, or exports. For culling, default to `--metadata-mode sidecar --no-organize --no-cleanup`; choose `--metadata-mode none` when the user wants no photo metadata change. Sidecar mode can create or update XMP; `--no-organize` is not a dry run, and both modes can still write reports or cache data. Do not embed metadata, move or delete originals, reset results, skip confirmation, or overwrite exports without the user's specific approval. Keep photos, coordinates, metadata, reports, and logs local; never upload or submit them on the user's behalf.

## Cull and inspect photos — `superpicky_cli.py`

| Command | Use |
| --- | --- |
| `info DIR` | Inspect existing results, rating statistics, and manifest presence. |
| `process DIR` | Cull and rate one folder. The CLI can organize photos unless you pass `--no-organize`. |
| `process DIR --resume` | Continue a saved run. Use only for the same verified target and checkpoint after the user chooses to resume; reapply the approved processing flags. Do not fall back to a fresh pass if resume fails. |
| `batch DIR --dry-run` | List folders in a recursive batch without running culling. Preview with the intended `--max-depth`; already-processed folders can still appear in the preview even if the actual run will use `--skip-existing`. |
| `batch DIR` | Cull the reviewed recursive scope. Processing flags are shared with `process`; batch adds `--skip-existing`, `--max-depth`, `--dry-run`, and `-y` / `--yes`. Do not use `-y` before approval. |
| `burst DIR` | Preview burst groups. Tuning options include `--min-count`, `--threshold` (milliseconds), and `--no-phash`. `--execute` can move files and update metadata; leave it off unless approved. |
| `reset DIR` / `batch-reset DIR` | Reset one folder or all processed folders under a root. May move files, clear metadata, and remove processing state. Confirm exact scope and backups; do not use as an automatic recovery step. |
| `identify IMAGE` | Identify a single image via the same engine and common flags as the standalone Bird ID CLI; no `--batch`. |

For `process`, press Ctrl+C once to request a cooperative stop; wait for it to report whether a checkpoint is available. A paused run exits with status 130. `--resume` only resumes pending photos: it does not replay unfinished final rating, Bird ID, or organization for photos already removed from the pending list. Check for partial results rather than treating a checkpoint as a finished batch.

Examples (replace paths; the `process` example writes sidecars and working data after approval):

```bash
python superpicky_cli.py info "/absolute/path/to/Birds"
python superpicky_cli.py batch "/absolute/path/to/Trip" --dry-run --max-depth 2
python superpicky_cli.py process "/absolute/path/to/Birds" --skill-level intermediate --auto-identify --ebird --birdid-country AU --birdid-region AU-SA --metadata-mode sidecar --no-organize --no-cleanup
```

### Processing options and defaults

- Most omitted settings come from `advanced_config`, which the GUI also uses. Explicit overrides apply to this invocation; defaults do not guarantee GUI parity. For tri-state options such as `--flight` / `--no-flight`, `--burst` / `--no-burst`, and `--exposure` / `--no-exposure`, omission follows configuration. `--exposure-threshold` tunes exposure detection.
- Use the `--skill-level` preset selected in the intake. Explicit `--sharpness`, `--nima-threshold` (TOPIQ aesthetics), `--confidence`, `--min-sharpness`, `--min-nima`, and `--picked-top` are advanced overrides, not interview questions.
- `--metadata-mode sidecar|none|embedded` governs global metadata writes; the legacy `--xmp` shortcut and `--arw-write-mode` are not a substitute for the global setting. If organization is approved, check `--folder-layout species-first|rating-first`. Use `--name-format` for the requested naming standard.
- Set `--auto-identify` (`-i`) to enable Bird ID during culling; sharing GUI configuration does not turn it on. It only applies to eligible photos (normally 2★ and above). Use `--birdid-country` (e.g. `AU`), `--birdid-region` (e.g. `AU-SA`), `--birdid-threshold`, and `--ebird` / `--no-ebird` as needed. These geographic filters narrow candidate species; they do not tag a photo with an observation country or submit anything to eBird.
- Review cleanup and storage explicitly: `--no-cleanup`, `--keep-temp-files` / `--no-keep-temp-files`, `--save-crop`, and on `process`, `--cleanup-days`. Retain logs and working state for verification and recovery.

## Identify species — `birdid_cli.py`

| Command | Use |
| --- | --- |
| `identify IMAGE [IMAGE ...]` | Identify one or more photos without metadata writes by default. `--batch` gives compact output; `--top` sets the number of candidates. Use `--no-yolo`, `--no-gps`, or `--no-ebird` only as needed. Use `--country` / `--region` to filter candidates. Leave `--write-exif` off unless explicitly approved; `--threshold` sets the minimum confidence for writing. |
| `organize DIR` | Move photos into species folders and record moves in `.birdid_manifest.json`. Review `--threshold` and geographic filters; do not pass `--write-exif` or `--yes` by default. |
| `reset DIR` | Restore photos organized by standalone Bird ID using `.birdid_manifest.json`. Inspect the manifest and confirm first; this is not SuperPicky culling reset. |
| `list-countries` | List supported country codes. |

```bash
python birdid_cli.py identify "/absolute/path/to/bird.jpg" --country AU --region AU-SA
```

## Export existing results or analyze video

Check the subcommand's local help first. `report` and `export-ebird` read an existing `.superpicky/report.db`; do not re-cull just to make these exports. Confirm a new absolute output path with an existing parent directory and leave `--force` off. For reports, default to `--text-only` (HTML without embedded previews, not a plain-text file); review the remaining contents for sensitive information. For eBird, confirm the observation location and country with the user; do not infer them from a Bird ID filter. Review the CSV locally and leave submission to the user.

```bash
python superpicky_cli.py report "/absolute/path/to/Birds" -o "/absolute/path/to/trip-report.html" --text-only
python superpicky_cli.py export-ebird "/absolute/path/to/Birds" -o "/absolute/path/to/checklist.csv" --location "Observation location" --country GB
python superpicky_cli.py video "/absolute/path/to/clip.mp4"
```

`video PATH` accepts a video or a non-recursive directory. Analyze without `--organize` by default. Adding `--organize` moves and renames original videos by species, so get separate approval. Use the subcommand help for frame sampling and species/flight detection settings.

## Verify and report

- For long runs, capture a local log and wait for completion. Check the exit status, final summary, and per-file or per-folder failures; do not equate an exit code alone with success. If interrupted, report the checkpoint state and ask before resuming or rerunning.
- After culling, use `info` and inspect the reported database or manifest when present. Count processed, rated, skipped, and failed items as available. Check sample outputs against the intended mode (for example, matching XMP files and unchanged original paths and contents when using sidecars without organization).
- For exports, confirm the exact output exists, opens as the expected format, and reflects the intended results; review checklist fields locally. For video or identification, check the actual analysis summary, low-confidence results, and failures. Report which files were written or moved and any output paths. Say which checks or counts were unavailable rather than inventing them.

CLI overview adapted from James Yu's [official SuperPicky CLI reference](https://superpicky.app/cli-reference).
