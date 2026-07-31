# SuperPicky 4.5.0 — RC history / RC 阶段增量记录

本文件归档 4.5.0 开发期各 RC 版本的**增量**说明（RC6 → RC11），仅供追溯。
面向用户的完整发布说明见仓库根目录 `ChangeLog.md`，其中已并入下列全部内容。

This file archives the per-RC **incremental** notes from the 4.5.0 development
cycle (RC6 → RC11) for traceability only. The user-facing release notes live in
`ChangeLog.md` at the repository root, which already incorporates everything
below.

---

# SuperPicky 4.5.0 RC11

**What's new since RC10:**

- **The Windows Lite build is discontinued.** Lite shipped a small installer and
  downloaded PyTorch and the models on first launch — but that made the *total*
  download larger, not smaller: roughly 1.2 GB across two stages (a 220 MB
  installer, then the wheels and 642 MB of models) against 792 MB for Full CPU
  in a single pass. The second stage depended on PyPI and mirror availability,
  which is exactly where it kept failing on Chinese networks. Windows now ships
  Full CPU on the Release page and Full CUDA through the file-sharing links.
  - **If you already run Lite, uninstall it before installing Full.** The two
    use different installer identities but the same install directory, so
    installing over the top leaves you with two SuperPicky entries in Add/Remove
    Programs. After uninstalling you can also delete the PyTorch runtime Lite
    downloaded under `%LOCALAPPDATA%`, which frees several GB — the Full build
    bundles its own and will never read it.

---

# SuperPicky 4.5.0 RC10

**What's new since RC9:**

- **Bird distributions rebuilt from GBIF observations.** Species filtering used
  AVONET native-range data, which systematically excluded naturalised species —
  in Sydney the house sparrow, feral pigeon, common starling and common blackbird
  were permanently masked and could never be identified, no matter how clear the
  photo. It also covered only 10,573 of the model's 10,964 classes, so 391
  species were unreachable everywhere on earth. Both datasets (AVONET and the
  bundled offline eBird lists) are replaced by one built from GBIF CC0/CC-BY
  occurrence records, covering 10,481 classes across 233 countries.
- **Filtering no longer collapses in sparse regions.** The old filter took a
  single candidate set and, when it was too narrow, gave up filtering entirely.
  An Iceland grid cell held just 54 species, which triggered exactly that — and
  produced Little Penguin, Blue-footed Booby and Horned Puffin on Faroe/Iceland
  photos. Candidates are now layered and widened one tier at a time (strong
  in-cell → all in-cell → 3×3 neighbourhood → country → unfiltered), so a sparse
  cell degrades smoothly instead of falling off a cliff. On a 433-photo
  Faroe/Iceland set all four cross-hemisphere errors are gone, while the
  identification rate *rose* from 63.3% to 65.4% — the fix removes errors, not
  recognition.
- **Every country is selectable now.** The country list is generated from the
  same dataset the filter uses, so the old three-way mismatch is structurally
  impossible: previously 11 of the 49 listed countries had no data at all
  (selecting them silently did nothing) while 14 countries with data could not
  be selected. Iceland and the Faroe Islands, absent before, are among the 233
  now available. The Settings Center also states plainly that a manually chosen
  country applies only to photos without GPS.
- **The Lite build gains geographic filtering.** It never shipped the AVONET
  database, so its filter silently did nothing while still carrying 1.5 MB of
  unused data. It now includes the new distribution database.
- **Smaller install.** Removing AVONET (102 MB) and the offline eBird lists
  (1.5 MB) in favour of a 35 MB database cuts roughly 68 MB from the full build.
- **Fixed: GPS was ignored for relative paths.** The metadata reader validated
  paths against the wrong working directory, so a relative path passed the check
  and then silently returned nothing. Photos processed through the CLI with a
  relative folder lost their GPS entirely — and with it all geographic
  filtering — without any error message.

---

# SuperPicky 4.5.0 RC9

**What's new since RC8:**

- **Star quotas re-tuned per skill level.** The 3-star share is now 40 % for
  Beginner, 30 % for Intermediate and 20 % for Master, with the 2-star share
  fixed at 30 % across all three — so the 1-star remainder grows from 30 % to
  50 % as the level gets stricter. Custom mode now starts from the Master
  preset (20 / 30) instead of 20 / 25. Note these are quota *ceilings*: the
  absolute sharpness floor, the eye-visibility cap and the per-burst cap all
  trim the actual count, so a batch will usually land below its quota.
- **Clearer wording for the rating reason.** The per-photo note used a single
  "top {n}%" phrasing for every star level, which read as a contradiction on
  1-star photos ("top 84%"). All three now read "rank {n}%" — the number was
  always a rank percentile, not a top-N share.
- **Distant birds no longer score inflated sharpness.** Head sharpness uses a
  gradient *density*, which rises as the bird gets smaller in frame — edges
  span fewer pixels, so a 60px head could out-score an 800px one on the very
  same subject. A controlled rescaling experiment measured the artifact at
  87.4 points per e-fold of head size (raw ∝ size^-0.641); that amount is now
  subtracted analytically. The correction is downward only and stops at 300px:
  photos with a head region at or above that size are untouched, so existing
  sharpness thresholds keep their meaning and normal framing is unaffected.
  On a distant-seabird set the sharpness/size correlation drops from -0.53 to
  +0.05. Note that head sharpness values for *small-in-frame* birds are no
  longer comparable with those recorded by earlier versions.
- **Star-quota split control (V2).** The single "3-star quota" slider is
  replaced by a three-segment quota bar. Drag its two dividers to set the
  3★ / 2★ / 1★ split directly — the three shares always add up to 100%, with
  1★ as the automatic remainder. It lives in both the Settings Center culling
  page and the home quick panel, kept in two-way sync. Ranges are bounded
  (3★ 5–50%, 2★ 5–60%, 1★ ≥ 5%); the "intermediate" preset keeps the previous
  20 / 25 / 55 behavior, so existing results are unchanged.
- **V1 / V2 rating toggle promoted.** The legacy V1 (absolute-threshold) rating
  switch moved out of the collapsed "Advanced" disclosure to a prominent spot
  right under the threshold heading. It's now visible without expanding
  anything and swaps the controls below in place — the quota bar for V2, the
  sharpness / aesthetics sliders for V1.
- **V2 caption / rating DB sync fix.** When V2 finalizes a photo's star and
  reason in the post-pass, both are now written back to `report.db`, so the
  browser's per-photo note no longer disagrees with its star rating.

---

# SuperPicky 4.5.0 RC8

**What's new since RC7:**

- **Best-of-burst pick, re-scored.** The frame chosen to represent a burst
  group is now selected by a tiered score — first the arbitrated focus tier,
  then eye clarity plus head sharpness — instead of head sharpness alone.
  This surfaces a sharper, better-focused keeper as the group's cover shot.
- **Species editing moved to the right-click menu.** The always-on edit
  pencil on grid tiles is gone; species correction/assignment now lives in
  each tile's right-click menu ("Edit Species…") and works for every photo,
  including ones without a name yet. Tile labels are cleaner as a result —
  a single line (species or filename), with the filename shown on hover.

---

# SuperPicky 4.5.0 RC7

**What's new since RC6:**

- **Faster, lighter full-screen browsing.** The full-screen viewer's preview
  pipeline was reworked. A resident parallel preload pool keeps held-arrow-key
  navigation on cache (0/25 → 25/25 reads), and high-resolution caching is now
  capped at the 3200px long edge and back-filled on a 250ms dwell — cutting
  resident memory by roughly 2.4 GB on large libraries. Also removed ~1050
  lines of dead results-browser code.
- **Settings Center additions.**
  - **Legacy V1 rating (opt-in).** A new "Advanced" section exposes the old
    absolute-threshold star rating (V1) for anyone who prefers it over the
    batch-relative V2 engine; the V2-only sliders hide when it is on.
  - **Bird-name display format.** Choose how species names are shown.
  - **Delete-confirmation toggle.** Turn the "confirm before deleting a photo"
    dialog on or off.
  - **Clear all preview caches.** A one-click button removes the current
    directory's AI preview/crop cache (`.superpicky/cache`) and the now-dangling
    cache paths in `report.db`, without touching your original photos.

---

# SuperPicky 4.5.0 RC6

**What's new since RC5:**

- **No-bird rescue scan (new).** When the first detection pass finds no bird
  at the default resolution, SuperPicky rescans at 1024px with a low
  threshold and uses the Bird ID classifier as a gatekeeper — recovering
  birds that YOLO missed (small, distant, or confused with airplane/kite)
  without letting false positives through. Toggle in Settings → Picking.
- **iRateBird species aesthetic index (new).** An offline, CC-BY beauty
  score (0–100) per species, shown in the detail panel and available as a
  filter/sort key. It is display-and-sort only and independent of the
  per-photo TOPIQ aesthetic score that drives star ratings.
- **Species correction entry (#106).** An edit pencil on the grid and detail
  cards lets you fix a misidentified species; candidate cards now follow the
  interface language.
- **Focus sharpness arbitration (#107).** When the EXIF focus-point verdict
  says a shot is soft but the measured bird-head sharpness clears your
  threshold, pixel evidence wins and the verdict is upgraded — fewer sharp
  keepers wrongly demoted.
- **Processing ~30% faster** (measured: 495 ARW, 135s → 95s). Proprietary
  RAW metadata now writes to XMP sidecars instead of rewriting the RAW body;
  fixed a bug that fully rewrote cached preview JPEGs on every photo, and a
  silent sidecar temp-file write failure.
- **Fixes.** English filter panel no longer clips the 0★ chip; the species
  aesthetic score is shown without a "/100" suffix.

---

# SuperPicky 4.5.0 RC6（中文）

**RC5 以来的新增：**

- **无鸟补救扫描（全新）。** 第一遍默认分辨率检测无鸟时，用 1024px 低阈值
  重扫、并以识鸟分类器守门——救回被 YOLO 漏检的鸟（小、远、或与飞机/风筝
  混淆），同时挡住误检。开关在 设置 → 精选。
- **iRateBird 鸟种颜值指数（全新）。** 离线 CC-BY 的鸟种颜值分（0–100），
  在详情面板展示、并可作为筛选/排序键。它仅用于展示与排序，与驱动评星的
  单张 TOPIQ 美学分相互独立。
- **鸟种纠错入口（#106）。** 网格卡与详情卡上新增编辑铅笔，可修正识别错误
  的鸟种；候选卡片跟随界面语言。
- **对焦锐度仲裁（#107）。** 当 EXIF 对焦点判定为脱焦、但实测鸟头锐度已过
  阈值时，以像素证据为准升级判定——减少清晰好片被误降级。
- **处理提速约 30%**（实测：495 张 ARW，135 秒 → 95 秒）。专有 RAW 元数据
  改写 XMP 侧车而非重写 RAW 本体；修复了每张照片都完整重写缓存预览 JPEG
  的 bug，以及侧车临时文件导致的静默写入失败。
- **修复。** 英文筛选面板不再裁掉 0★ 筹码；鸟种颜值分去掉「/100」后缀。
