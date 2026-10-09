# SuperPicky 4.6.4 RC1

**What's new since 4.6.3:**

- **Fixed: screenshot identification on Windows.** Clicking *Screenshot* opened the
  Windows snipping tool, but the capture was then silently dropped and never
  identified (#113). It now goes straight to identification, and SuperPicky waits up
  to 3 minutes for you to take the shot instead of 60 seconds. macOS was not affected.
- **Paste to identify.** Press **Ctrl+V** (**⌘V** on Mac) in the Bird ID panel to
  identify whatever image is on the clipboard — a screenshot from any tool, or an image
  file you copied in Explorer or Finder. Typing in a text box still pastes text as usual.
- **Search species by code** (#119). In the species editor and the bird-name lookup you
  can now type an **eBird species code**, which every species has (`cangoo` → Canada
  Goose, `gragoo` → Greylag Goose; a prefix such as `cang` works too), or a North
  American **4-letter alpha code** (`CANG`). Exact code matches are listed first; Chinese
  names and pinyin initials still take priority. In the bird-name lookup this works when
  the *SuperPicky* catalog is selected.
- **For tools that use the Bird ID API** (Lightroom plugin, SuperViewer and similar):
  each candidate now also carries its pinyin, GBIF rarity and IUCN status, and a new
  `all_results` list returns every candidate before the usual filtering, while
  `results` is unchanged. Contributed by @OscarKing888 (#114, #115).

---

# SuperPicky 4.6.4 RC1（中文）

**4.6.3 以来的变化：**

- **修复：Windows 上截图识别不工作。** 点「截图识别」能调出系统截图工具，但截好的图
  随后被悄悄丢掉，始终不开始识别（#113）。现在截完直接识别，等你截图的时间也从 60 秒
  放宽到 3 分钟。Mac 不受影响。
- **粘贴识别。** 在识鸟面板按 **Ctrl+V**（Mac 为 **⌘V**），直接识别剪贴板里的图片——
  任何截图软件截的图，或在资源管理器 / 访达里复制的图片文件都行。在输入框里粘贴文字
  照常不受影响。
- **用鸟种代码搜索**（#119）。改鸟种弹窗和鸟名查询里可以直接输入 **eBird 物种代码**
  （每个鸟种都有，`cangoo` → 加拿大黑雁，`gragoo` → 灰雁；输入前缀如 `cang` 也行），
  或北美的 **4 位代码**（`CANG`）。代码精确命中的排在前面，中文名和拼音首字母仍然
  优先。鸟名查询里需选中「SuperPicky 名录」才生效。
- **给调用识鸟接口的工具**（Lightroom 插件、SuperViewer 等）：每个候选新增拼音、GBIF
  罕见度和 IUCN 等级；新增 `all_results` 列表，返回常规筛选之前的全部候选，原有的
  `results` 不变。由 @OscarKing888 贡献（#114、#115）。

---

# SuperPicky 4.6.3

This release is about correcting the AI when it is plainly wrong, about bird
names that agree everywhere you see them, and about a new Traditional Chinese
(Taiwan) interface.

## What's new

1. **Traditional Chinese (Taiwan) interface.** A new `繁體中文 TW` language with
   Taiwan wording throughout. Bird names use the Taiwan names from the IOC list
   (e.g. 白頭翁, 黑面琵鷺), and so do the folders SuperPicky creates
   (`3星_優選`, `其他鳥類`) and the titles and keywords written for Lightroom and
   Apple Photos. You can search by Taiwan names too. It is picked automatically
   when your system language is Traditional Chinese. Reset, re-scan and burst
   sorting still recognise folders created in any of the three languages.

2. **Choose the language in Settings, and everything follows it.** The bottom of
   the Settings sidebar has an always-visible language picker: Follow system,
   简体中文, 繁體中文 TW or English. Restart the app after changing it. Messages,
   logs and both command line tools (`superpicky_cli`, `birdid_cli`, including
   `--help`) now use the chosen language; previously about 350 messages were
   fixed in one language.

3. **Tell SuperPicky "this is not a bird".** Every so often the detector calls a
   crocodile — or a branch, or a rock — a bird, and until now you could change
   the species but not remove it. The species dialog now has a **Not a bird**
   button. It clears the species, drops the photo to 0 stars, moves it to the
   reject pile and takes it out of the report's species list, all in one step.
   Tick several thumbnails first and the whole selection goes at once. Got it
   wrong? Just set a species again.

4. **Re-identify a photo from the species dialog.** When a photo landed in
   "other birds", or the identification simply looks wrong, **Re-identify this
   photo** re-runs the models on the full image — detecting the bird afresh
   rather than reusing the crop made during culling — and lists every candidate
   with its confidence, including low ones.

5. **The species picker opens with the birds you actually photographed.** A
   misidentified bird is usually mistaken for a species you also shot that day,
   so the dialog now lists this shoot's species first, most-shot at the top;
   once you type, they still sort to the top of the matches. It searches the
   same names Bird ID uses, and old or alternative names still find the bird.

6. **"Other birds" shows what Bird ID was unsure about.** Photos below the
   confidence threshold still go to "other birds", but thumbnails and the detail
   panel now read "Species name (unconfirmed N%)", and 2-star-and-up photos get
   the same line in their EXIF title (as a title only, never as a keyword).
   Candidates below 30% are not shown.

7. **A burst is no longer split across two species folders.** Frames of one burst
   could be identified as different species, sending one burst into two folders.
   The whole burst now takes the species of its most confident frame, before
   star ratings are assigned, so quotas, folders and the report all see one
   species.

8. **Bird ID's location filter uses eBird's regional checklists.** For China,
   Australia and the United States you can again pick a province or state in
   Settings, and when a photo carries GPS the region is worked out offline from
   the coordinates. Overseas territories use their own checklist instead of the
   mother country's. The 35 MB grid database this replaces is gone, so the
   download is that much smaller.

9. **Picks show as Pick flags in Lightroom Classic** (13.2 or later). Before, the
   crown pick was never recognised there. Photos with no bird now show as
   Rejected. For photos already in your catalog, use *Metadata → Read Metadata
   from Files* to pick up the change.

10. **Captions explain the rating.** The Lightroom caption and the browser's
    culling note open with the verdict and why — the photo's rank among shots of
    the same species and where the star cut-offs fall, or which rule kept it from
    3★ — then each check with ✓/✗. The detail panel, sorting, the Bird ID panel
    and exported reports all show the same values the rating actually uses.

11. **Reopening a processed folder brings back what you saw last time.** The
    console replays that folder's log, the Bird ID panel shows the completion
    summary again, and dropping an already-processed photo into the panel shows
    what the previous run recorded instead of decoding the RAW and re-running the
    models. Contributed by @OscarKing888 (#109). The summary also reflects any
    species, rating or no-bird changes you made in the browser.

12. **One set of Chinese bird names everywhere.** SuperPicky, the 慧眼观鸟 app,
    OZBirds and the eBird Chinese-name extension now take their names from one
    shared database, so Bird ID results, the species dialog and the name lookup
    no longer disagree. Names follow IOC 15.1, with the current ChinaBirds
    checklist for species that occur in China (thanks to @lhy1024, #110). Where a
    recent split is still one species to the ID model, the whole-species name is
    kept; newly split species the model does not know yet (e.g. Tasmanian
    Boobook) can still be picked, labelled "Not in ID model".

    **Four names now belong to a different species.** Folders, the report's
    species list and the eBird export key on the Chinese name, so this matters if
    you have older batches:

    | Name | Used to mean | Now means |
    |---|---|---|
    | 大山雀 | Parus major (Great Tit) | Parus cinereus (Cinereous Tit) |
    | 灰眉岩鹀 | Emberiza cia (Rock Bunting) | Emberiza godlewskii |
    | 虎斑地鸫 | Zoothera dauma (Scaly Thrush) | Zoothera aurea |
    | 红眉朱雀 | Carpodacus davidianus | Carpodacus pulcherrimus |

    Batches processed before this update keep the names they were filed under;
    nothing is renamed on disk. Browsing several batches together groups by name,
    so old and new batches using the same name show as one species. The eBird
    export cross-checks Chinese and English names, so affected records land in
    its "needs verification" list.

13. **Chinese bird names show their pinyin** (Simplified Chinese interface only)
    in the name lookup, the species dialog, the detail panel and Bird ID result
    cards. Readings are tone-marked and were checked by hand where sources
    disagreed.

14. **"Write into the file" can now cover proprietary RAW.** NEF, CR2, CR3, ARW,
    RAF, ORF, RW2, PEF, 3FR and IIQ have always received an `.xmp` sidecar
    instead, so software that does not read sidecars (Nikon NX Studio, for
    example) never saw any of it. Settings → Output now has **Also write into
    proprietary RAW files**, off by default. With it on, each photo takes about
    0.2 s longer and is verified on a copy first, so the original is never left
    damaged; on memory cards and external drives that copy is noticeably slower.

15. **An Export menu in the browser toolbar.** Share report, eBird records and
    import to Apple Photos now sit together under **Export ▾**, and each item says
    which photos it covers and what it produces. Toolbar buttons line up, and
    every control has a tooltip explaining what it does.

16. **Changing a species also changes its rarity, conservation status and beauty
    score**, looked up again from the new species — for single photos, multiple
    selections, whole-species merges and every frame of a burst. Before, a photo
    corrected to a Barn Swallow could top the report wearing the old species'
    "very rare" and "endangered" badges.

17. **Emptied folders are removed.** After changing a species or a rating in the
    browser, species and rating folders left empty are deleted, and processing no
    longer leaves empty rating folders behind when bursts are gathered into a
    `burst_` folder. Only empty folders and system files (`.DS_Store`,
    `Thumbs.db`, `desktop.ini`, orphaned `._` files) are ever removed; photos,
    sidecars and your own files are never touched.

18. **Smaller conveniences.** The results browser opens maximized; the folder
    chooser accepts several folders at once when merging; the whole-species merge
    confirmation says how many batches it spans; your own rarity index shows next
    to the global one in the name lookup, and on its own for species the global
    index does not cover; Singapore is in the country dropdown's top-10, and
    "More countries" search matches Chinese names and country codes in any
    interface language.

19. **The NVIDIA GPU (CUDA) installer is on the GitHub release page and is
    smaller.** It was long believed to exceed GitHub's 2 GiB file limit and was
    only on cloud drives; it never did (it is 1.9 GiB). A cuDNN component this
    app never uses has been removed, saving about 100 MB. Every download now comes
    from GitHub, with a China mirror on the website for faster downloads in
    mainland China.

## Fixes

20. **Corrections now reach the report, and stay put.** After you corrected a
    species, the exported report, the eBird export and the "birds you
    photographed this session" list still showed the old one; expanding a burst
    group or paging in full screen could bring the old name back; and the
    report's star breakdown counted a photo's old rating. All of these now follow
    your edits, and the report's species list uses the same 2-star line as the
    browser's species dropdown and the eBird export, so all three agree.

21. **The main window no longer opens with its buttons off-screen.** A window
    saved while the Dock was hidden came back with **Start** and **Reset** under a
    pinned Dock. The saved placement is now kept within the screen's usable area.

22. **78 common species are no longer labelled "legendary".** A flaw in matching
    rarity data scored them as impossibly rare — the Eastern Cattle Egret among
    them. All affected entries have been rebuilt.

23. **XMP sidecars no longer appear with metadata writing switched off.** Changing
    a star rating or marking a photo as no-bird ignored the setting and wrote an
    `.xmp` every time.

24. **Dropping a NEF into Bird ID no longer opens a second copy of the app** on
    the Mac, with the original panel spinning forever.

25. **The species line no longer vanishes from photo descriptions.** The rating
    pass used to overwrite the "Species:" and "Alternative species" lines.

26. **Rating V2 checks focus on every photo.** Photos below your V1 sharpness
    threshold were never checked and were all shown as in focus.

27. **Smaller fixes.** Changing a species updates the thumbnail caption straight
    away; a file move that fails during a correction is now reported instead of
    looking like success; the *Species beauty* sort is remembered; and the
    Windows installer's memory use during extraction has been lowered to fix an
    "Out of memory" error on some Windows 11 machines (#112 — we could not test
    this on Windows ourselves, so please tell us if you still see it).

---

# SuperPicky 4.6.3（中文）

这一版是关于三件事：AI 认错得离谱时你能纠正它，同一种鸟在各处叫同一个名字，
以及新增的繁体中文（台湾）界面。

## 这一版有什么新东西

1. **新增繁体中文（台湾）界面。** 语言选项「繁體中文 TW」，全程台湾用词。鸟名用
   IOC 名录里的台湾叫法（如 白頭翁、黑面琵鷺），SuperPicky 建的文件夹
   （`3星_優選`、`其他鳥類`）和写给 Lightroom / Apple Photos 的标题、关键词也一样；
   也能用台湾鸟名搜索。系统语言是繁体中文时自动选用。重置、重扫与连拍整理仍认得
   任何一种语言建的文件夹。

2. **在设置里选界面语言，处处跟着变。** 设置中心左栏底部常驻语言选择：跟随系统、
   简体中文、繁體中文 TW、English，切换后重启生效。提示、日志和两个命令行工具
   （`superpicky_cli`、`birdid_cli`，含 `--help`）都按所选语言显示；此前约 350 处
   文字写死成一种语言。

3. **可以告诉 SuperPicky「这不是鸟」。** 识别偶尔会把鳄鱼——或者一根树枝、一块
   石头——当成鸟，而此前你只能改鸟种，不能说「这压根不是鸟」。改鸟种的弹窗里现在
   多了**「这不是鸟」**：鸟种清空、降为 0 星、移进废片堆，报告的鸟种名录里也不再
   有它，一步到位。先勾几张再点，整批一起标掉。标错了？重新指定鸟种即可。

4. **可以在改鸟种弹窗里重新识别这张照片。** 照片被归进「其他鸟类」、或识别结果
   不对劲时，点**「重新识别这张」**会对整张图重跑模型——重新检测鸟的位置，而不是
   沿用选片时裁好的框——并列出全部候选及其置信度，低置信度的也列。

5. **选鸟种时先列出你今天真拍到的那些。** 认错多半是认成了当天也拍到的隔壁那种，
   所以弹窗打开时先按张数列出本次拍到的鸟种；开始搜索后，它们仍排在匹配结果最前。
   搜的就是识鸟用的那套鸟名，搜旧名或别名照样能找到。

6. **「其他鸟类」会显示识鸟没把准的那个鸟种。** 置信度低于阈值的照片仍归入
   「其他鸟类」，但缩略图和详情面板现在会显示「鸟名（待确定 N%）」，2 星及以上的
   照片也把同样的文字写进 EXIF 标题（只写标题、不写关键字）。置信度低于 30% 的
   候选不显示。

7. **同一组连拍不会再被拆进两个鸟种目录。** 逐帧识鸟可能给同一组连拍判出不同鸟种。
   现在整组统一采用置信度最高那一帧的鸟种，而且发生在评星之前，所以配额、分目录和
   报告看到的都是同一个鸟种。

8. **识鸟的地理过滤改用 eBird 区域清单。** 中国、澳大利亚、美国重新可以在设置里
   选省/州；照片带 GPS 时，离线根据坐标判断区域。海外领地改用自己的清单，不再并入
   宗主国。被替换掉的网格库有 35 MB，下载包也相应变小。

9. **精选（皇冠）在 Lightroom Classic 里显示为「留用」旗标**（13.2 及以上）。此前
   Lightroom 从未识别出精选。无鸟照片今后显示为「排除」。已导入目录的照片，请用
   「元数据 → 从文件读取元数据」更新。

10. **题注说清楚为什么是这个星级。** Lightroom 题注与浏览器选片备注第一行是结论和
    原因——在同种照片里排第几、各星级的分界在哪，或者卡在哪条规则没拿到 3★——再列
    ✓/✗ 逐项依据。详情面板、排序、识鸟面板和导出报告显示的都是评星实际用的数值。

11. **重新打开处理过的目录，会恢复上次那一屏。** 控制台回放该目录的日志，识鸟面板
    重新显示完成统计；把已处理过的照片拖进面板，直接显示上次的结果，不再重新解码
    RAW、重跑模型。由 @OscarKing888 贡献（#109）。在浏览器里改过的鸟种、星级或无鸟
    标记，完成统计也会跟着更新。

12. **各处中文鸟名统一为同一套。** SuperPicky、慧眼观鸟、OZBirds 与 eBird 中文鸟名
    插件现在共用同一个鸟名库，识鸟结果、改鸟种弹窗和鸟名查询不再各说各的。鸟名以
    IOC 15.1 为准，中国有分布的鸟种用现行 ChinaBirds 名录（感谢 @lhy1024，#110）。
    模型仍当作一个种的新拆分种保留整种名；模型还不认识的新拆分种（如塔岛鹰鸮）
    仍可选，并标注「识鸟模型未收录」。

    **其中 4 个名字现在指的是另一个鸟种了。** 分目录、报告的鸟种清单、eBird 导出
    都以中文鸟名为准，所以如果你有旧批次，这条值得看一眼：

    | 名字 | 原来指 | 现在指 |
    |---|---|---|
    | 大山雀 | Parus major（欧亚大山雀） | Parus cinereus（苍背山雀） |
    | 灰眉岩鹀 | Emberiza cia（淡灰眉岩鹀） | Emberiza godlewskii（戈氏岩鹀） |
    | 虎斑地鸫 | Zoothera dauma（小虎斑地鸫） | Zoothera aurea（怀氏虎鸫） |
    | 红眉朱雀 | Carpodacus davidianus（中华朱雀） | Carpodacus pulcherrimus |

    之前处理的批次仍沿用当时归档的名字，磁盘上不会有任何目录被改名。合并浏览多个
    批次时按名字分组，旧批次与新批次用同一个名字会显示成同一个鸟种；eBird 导出会
    交叉核对中英文名，受影响的记录会进「待核对」清单。

13. **中文鸟名会显示汉语拼音**（仅简体中文界面），在鸟名查询、改鸟种弹窗、详情面板
    和识鸟结果卡片四处。拼音带声调，数据源有分歧的地方都经过人工核对。

14. **「写入文件」现在可以对专有 RAW 生效。** NEF、CR2、CR3、ARW、RAF、ORF、RW2、
    PEF、3FR、IIQ 一直只写 `.xmp` 边车，于是不读边车的软件（例如尼康 NX Studio）
    看不到这些信息。设置 → 输出新增**「专有 RAW 也写入文件本体」**，默认关闭。打开
    后每张约多 0.2 秒，并先在副本上校验，原文件不会被留在损坏状态；在存储卡和外置盘
    上这次复制会明显更慢。

15. **浏览器工具栏新增「导出」菜单。** 分享报告、eBird 记录、导入「照片」App 收进
    **「导出 ▾」**，每项写明作用于哪些照片、产出什么。工具栏按钮对齐，每个控件都有
    说明功能的提示。

16. **改鸟种时，罕见度、IUCN 等级和鸟种颜值会跟着一起改**，按新鸟种重新查——单张、
    多选、整种合并和整组连拍都一样。此前一张被改成「家燕」的照片，会顶着旧鸟种的
    「极罕见 + 濒危」标记排在报告最前面。

17. **搬空的文件夹会被删掉。** 在浏览器里改鸟种或星级后，搬空的鸟种目录与星级目录会
    删除；处理时连拍归入 `burst_` 目录后，也不再留下空的星级目录。只删空目录与系统
    文件（`.DS_Store`、`Thumbs.db`、`desktop.ini`、孤立的 `._` 文件），照片、边车
    文件和你自己的文件一概不碰。

18. **几处小改进。** 选鸟结果浏览器默认最大化打开；合并目录时可以一次选中好几个；
    整种合并的确认弹窗会写明跨了几个批次；鸟名查询并排显示你自己的罕见指数，全球
    罕见度没有收录的鸟种也能单独显示；国家下拉的 Top10 加入新加坡，「更多国家」在
    任何界面语言下都能用中文名和国家代码搜索。

19. **N 卡（CUDA）安装包直接放在 GitHub 发布页上，而且变小了。** 它一直被以为超过了
    GitHub 单文件 2 GiB 上限，只能放网盘；其实从来没超过（1.9 GiB）。另外去掉了一个
    程序用不到的 cuDNN 组件，省下约 100 MB。所有安装包今后都从 GitHub 发布，官网另提供
    大陆镜像，国内下载更快。

## 修复

20. **纠错会进到报告里，而且不会变回去。** 改完鸟种后，导出的报告、eBird 导出和
    「本次拍到的鸟种」列表仍是旧鸟种；展开连拍组或全屏翻页可能让旧鸟名回来；报告的
    星级分布按旧星级统计。现在这些都跟着你的修改走，而且报告的鸟种清单与浏览器的
    鸟种下拉、eBird 导出采用同一条 2 星线，三处一致。

21. **主窗口不会再把按钮开到屏幕外面。** 在 Dock 自动隐藏时存下的窗口位置，等 Dock
    固定显示后再打开，**「开始处理」**和**「重置」**会被压在下面。现在记住的位置会
    限制在屏幕可用区域内。

22. **78 个常见鸟种不再被标成「传奇」。** 罕见度数据匹配有误，把它们算成了稀世罕见
    ——牛背鹭就在其中。所有受影响的条目都已重建。

23. **关掉元数据写入后，不再生成 XMP 边车。** 改星级和标记无鸟此前不检查这个设置，
    每操作一次就写一个 `.xmp`。

24. **在 Mac 上把 NEF 拖进识鸟面板，不会再多开一个程序**、原面板一直转圈。

25. **照片说明里的鸟种行不再消失。** 评星环节此前会抹掉「鸟种：」「备选鸟种」两行。

26. **评星 V2 对每张照片都检查对焦。** 锐度低于 V1 门槛的照片此前从不检查，一律显示
    为合焦。

27. **其他修复。** 改完鸟种，缩略图下的鸟名立刻更新；纠错时文件移动失败会提示出来，
    不再看起来和成功一样；「鸟种颜值」排序会被记住；Windows 安装包解压时的内存占用
    已降低，修复部分 Windows 11 机器上报「Out of memory」的问题（#112——我们没有
    Windows 机器实测，如果你仍遇到，请告诉我们）。

---

# SuperPicky 4.6.2

This release is about looking at more than one shoot at a time: pick any folders
you like — from any drive — and see them as a single set of results.

## What's new

1. **Merge several folders into one set of results.** Open a folder that holds
   more than one processed batch and SuperPicky now asks which ones you want,
   then shows them together: one photo count, one species count, one report. The
   list is yours to build — an **Add Folder…** button lets you keep adding
   folders from anywhere, including other drives, so the days you want to
   combine no longer have to sit under a common parent. Add a parent folder and
   it offers to pull in every processed batch inside it at once. Each row shows
   that folder's photo and species count before you commit to opening it, and
   you can remove any row you did not mean to add. The same **Merge Folders…**
   button sits in the browser toolbar, so you can widen or narrow the set at any
   time without going back to the main window.

2. **The report now lists every species up front.** Under the "Species (N)"
   heading there is now a compact index of every species and how many photos you
   took of it, in the same rarity order as the gallery below. Click a name to
   jump straight to its block. The gallery shows at most four frames per
   species, so the per-species totals were previously nowhere to be found.

3. **The species dropdown is numbered.** The filter's species list now numbers
   its entries, so the last number tells you how many species the batch holds
   without counting them yourself — useful when ten merged days run to forty or
   fifty species.

## Fixes

4. **Merging more than ten folders no longer silently drops data.** SQLite
   allows at most ten databases to be attached at once, and the eleventh onward
   failed silently: the photo and species counts covered only the first ten
   folders, with nothing to indicate the rest were missing. Wrong numbers that
   look right are worse than an error message. Every folder is now queried on
   its own, with no limit.

5. **Folders processed by older versions can be merged again.** Databases
   written by different versions do not all have the same columns, and one
   folder with four leftover columns from an abandoned feature was enough to
   make the whole merge fail with an unreadable SQL error. Columns are now
   matched by name: missing ones read as empty, extra ones are ignored.

6. **A batch nested inside another is no longer counted twice.** If a processed
   folder contains another processed folder of the same photos — which happens
   when a batch is re-run into a subfolder — both used to be included, inflating
   every total. Only the outer one is kept now. A batch inside an *unprocessed*
   folder (a camera card folder, say) is still counted, because there it is the
   real batch.

7. **Merging folders across different drives works.** Combining a folder on an
   external drive with one on the internal drive used to fail outright on
   Windows. This is a common way to work — today's shoot on the portable drive,
   last week's already copied to the internal one — so it now simply works.

---

# SuperPicky 4.6.2（中文）

这一版是为了「一次看不止一次外拍」：你可以自由挑任意几个目录——哪怕在不同硬盘
上——把它们当成一份结果来看。

## 这一版有什么新东西

1. **把几个目录的选鸟结果合成一份。** 打开一个含多个已处理批次的文件夹时，
   SuperPicky 会先问你要哪几个，然后合起来显示：一个总张数、一个鸟种数、一份
   报告。这份清单完全由你决定——**「添加目录…」**按钮可以一直往里加，任意位置、
   任意硬盘都行，要合并的那几天不必挤在同一个父目录下。加进来的若是父目录，它会
   问一句要不要把里面的批次全部加入。每一行都先写明该目录有多少张、多少种，你
   不必打开就知道量有多大，加错了也可以单独移除。工具栏上有同样的**「合并目录…」**
   按钮，看完一天之后想把前几天也算进来，随时可以改，不用退回主界面。

2. **报告开头先列出所有鸟种。** 「本次鸟种 (N)」标题下多了一段名录：每个鸟种加
   它的张数，顺序与下方画廊一致（按罕见度）。点鸟名直接跳到对应的图片区块。画廊
   每种最多放 4 张，所以某种到底拍了多少张，以前在报告里根本查不到。

3. **鸟种下拉带序号了。** 筛选栏的鸟种列表逐项编号，拉到底看末位序号就知道这批
   有多少种，不必自己数——合并十天常有四五十种。

## 修复

4. **合并超过 10 个目录不再静默丢数据。** SQLite 一次最多只能挂 10 个数据库，
   第 11 个起会失败，而失败被吞掉了：你看到的张数和鸟种数只含前 10 个目录，界面
   上却没有任何提示。看起来正常的错数字比报错危险得多。现在每个目录单独查询，
   没有数量上限。

5. **老版本处理过的目录又能参与合并了。** 不同版本写的数据库列数不一样，只要有
   一个目录残留着某个已废弃功能的四个字段，整个合并就会抛一句读不懂的 SQL 错误
   而失败。现在按列名对齐：缺的算空，多的忽略。

6. **嵌套在里面的重复批次不再被算两遍。** 一个已处理目录里若还套着另一个装着同一
   批照片的已处理目录（把同一批重跑进子目录时会出现），原先两个都算，所有合计
   数字都会翻倍。现在只取外层。若外层目录本身没被处理过（比如相机卡目录），
   里层仍然照常计入——那才是真正的批次。

7. **跨硬盘合并目录能用了。** 把移动盘上的一个目录和内置盘上的一个目录合在一起，
   在 Windows 上原先会直接失败。而这恰恰是常见的用法——当天的在移动盘、上周的
   早已拷进内置盘——现在正常工作。

---

# SuperPicky 4.6.1

This release is about getting your sightings out of the app and into eBird, and
about making species corrections actually stick.

## What's new

1. **Export your sightings to eBird.** The results browser has a new Export
   eBird button. It turns the birds you photographed into an eBird checklist
   file you can upload on the eBird website — one checklist per shooting day,
   one row per species, count of 1. You type the location name once; the
   coordinates come from your photos' GPS automatically (the median of that
   day's fixes, so one stray reading cannot drag the location off). Only photos
   rated 2 stars or higher with an identified species are included. Species are
   identified by scientific name, which works regardless of what display
   language your eBird account uses — a common name that is correct globally can
   still be rejected by an Australian or British account, and this avoids the
   whole problem. Upload it on eBird under Import Data → eBird Record Format
   (Extended).

2. **Show a second rarity figure of your own.** If you have your own rarity
   dataset — a 0 to 10 score per species from any source you trust — you can
   import it in Settings → Bird ID, and the detail panel will show it next to
   the built-in global rarity, like "Legendary (91.5 - 7.97)". SuperPicky ships
   no such data; it only provides the slot. Star ratings and sorting are
   unaffected — this is for your reference only.

3. **RAW extraction now reports progress and can be stopped.** Extracting
   previews from a large batch of RAW files used to look frozen. It now shows
   progress as it goes, and the Stop button works during that stage instead of
   only after it.

## Fixes

4. **Changing a bird's species now actually moves the photo.** Correcting a
   species used to update the name but leave the file in the old species folder
   — and a second correction on the same photo would silently do nothing at all.
   Both are fixed. The correction also now writes the new name into the photo's
   XMP metadata (title and keywords), so Lightroom and other tools see it too;
   previously the file on disk kept the wrong name forever.

5. **Correct several photos at once.** Tick multiple thumbnails, right-click,
   and the species change applies to all of them. Burst groups are handled as a
   whole, so correcting one frame corrects the entire burst.

6. **Fixed a random crash.** The app could abort at unpredictable moments
   because thumbnail loading threads were destroyed while still running. This
   accounted for half the crash reports collected on the development machine.

7. **The Video page is back in Settings.** It had been removed from the
   settings navigation while the underlying feature was still active, which left
   the video toggle unreachable for anyone who had not turned it on before
   upgrading.

8. **Cross-folder burst merging no longer creates folders from low-confidence
   names.** A burst spanning two folders could end up filed under a species name
   the identifier was not confident about.

---

# SuperPicky 4.6.1（中文）

这一版的重点是把你的观测记录送进 eBird，以及让「改鸟种」真正生效。

## 这一版有什么新东西

1. **导出 eBird 观测记录。** 选鸟结果浏览器新增「导出 eBird」按钮，把你拍到的
   鸟整理成 eBird 清单文件，可直接在 eBird 网站上传。按拍摄日期一天一份清单，
   同一天同一鸟种一行，数量固定 1。地点名你填一次，坐标自动取自照片 GPS（取
   当天的中位数，个别漂移点不会把位置带偏）。只统计 2 星以上、已识别出鸟种的
   照片。鸟种用学名标识，因此不受你 eBird 账号显示语言的影响——一个全球通用
   的英文名在澳洲或英国账号下仍可能被拒收，用学名可以完全绕开这个问题。上传
   时在 eBird 选「Import Data」→「eBird Record Format (Extended)」。

2. **可以显示你自己的第二套罕见度。** 如果你手上有一份自己信得过的鸟种罕见度
   数据（每种 0 到 10 分），可以在「设置 → 识鸟」里导入，详情页就会把它显示在
   内置的全球罕见度旁边，形如「传奇 (91.5 - 7.97)」。SuperPicky 本身不附带任何
   这类数据，只提供接口。评星与排序完全不受影响，纯属参考。

3. **RAW 提取阶段现在有进度，也能中途停止。** 处理大批 RAW 时的预览提取过去
   看起来像卡住了，现在会持续报告进度，「停止」按钮在这一阶段也能立即响应，
   不必等它跑完。

## 修复

4. **改鸟种现在真的会把照片移过去。** 过去改完鸟种，名字变了但文件还留在原来的
   鸟种目录里；对同一张照片改第二次更是完全没有反应。两个问题都已修复。改鸟种
   现在还会把新鸟名写进照片的 XMP 元数据（标题与关键字），Lightroom 等软件也能
   看到——过去磁盘上的文件会一直保留着错误的鸟名。

5. **可以一次改多张。** 勾选多张缩略图后右键改鸟种，会一次性全部改掉。连拍组
   整组处理，改其中一张等于改整组。

6. **修复了一个随机崩溃。** 缩略图加载线程在仍然运行时被销毁，会让程序在不确定
   的时刻整个退出。开发机上收集到的崩溃报告有一半源于此。

7. **设置中心的「视频」页回来了。** 之前它被从设置导航里摘掉，而视频功能本身
   还在运行，导致升级前没开过该功能的人再也打不开这个开关。

8. **跨目录连拍合并不再用低置信度鸟名建目录。** 跨两个目录的连拍组可能被归到
   一个识别器本身并不确定的鸟种名下。

---

# SuperPicky 4.6.0

This release is about getting your results out of the app: export a shareable
report of the day's shoot, send your keepers to Apple Photos, and fix a whole
misidentified species in one go.

## What's new

1. **Export a shareable report of your shoot.** The results browser has a new
   Export Report button. It produces a single HTML file in your picking folder
   that opens by double-clicking, with the photos embedded inside it — send it
   to a friend, post it in a group, or keep it as your own record. It works
   offline and the images never go missing. The report opens on your best frame
   of the day, then gives each species its own section ordered by rarity, with
   its Chinese and scientific names, a rarity badge, an IUCN badge for
   threatened species, and up to four photos. Burst frames are collapsed to one
   per burst, so you get four different moments rather than four near-identical
   ones. Every photo carries its exposure settings, and the lead shot of each
   species also shows its sharpness, aesthetics and species beauty scores.
   Below that is a breakdown of your stars, keeper rate, in-flight and sharp
   counts, burst groups and gear. Click any photo to enlarge it. A Save as PDF
   button prints it on white paper. A typical shoot — 284 photos, 12 species —
   comes to about 4 MB.

2. **Send your keepers straight to Apple Photos (macOS only).** The results
   browser has an Add to Photos button. It imports the RAW file whenever one
   exists, and writes the bird's name, your star rating and the quality figures
   into the Photos title, description and keywords, so you can search for a
   species inside Photos itself. Photos you have already sent across are
   skipped, so running it a second time will not duplicate anything. If you have
   ticked any thumbnails, only those are imported; if you have ticked none, the
   whole filtered list goes. Each run creates or reuses an album named after the
   folder and the date, filed under a SuperPicky Imports folder. Your RAW files
   are never modified, and XMP sidecars are never sent to Photos. Contributed by
   @orientaldollarbird.

3. **Fix a whole misidentified species in one go.** When a batch gets the same
   bird wrong from end to end, right-click any of those photos and pick
   Change all <species> to…. It retags every photo of that species in the
   database — not just the ones currently filtered on screen — and moves them
   into the new species' folders, keeping burst groups together. Before anything
   moves you get a confirmation showing how many photos are involved, how many
   burst groups, and the exact target folders, so a batch organised in English
   won't quietly grow a second set of folders in Chinese. Related fix: changing
   a species used to fail silently when a file with the same name already sat in
   the target folder — the database was updated while the file stayed put. Now
   the file and the database never disagree.

4. **4 and 5 stars get their own folders.** Photos you promote by hand are no
   longer filed with the 3-star ones, and the keyboard now goes all the way to
   5. Your manual promotions also count in the statistics: the keeper rate is
   now 3 stars and above, so promoting a photo no longer makes the number go
   down.

5. **Picked only is its own switch, and your picks always sort first.** The
   crown used to sit in the row of star filters, where it looked like it added
   photos to the list — it actually cut the list down to just your picks. It is
   now a separate checkbox under that row. And because a pick is the overlap of
   the sharpest and the best-looking of your 3-star shots, sorting by sharpness
   or rarity alone used to scatter them: in one test the twelve picks landed at
   positions 2, 4, 8 … 44, and as far down as 120 when sorted by rarity. Picks
   now always come first, with your chosen sort applied inside them. Sorting by
   filename is left alone, since its whole point is shooting order.

6. **Anonymous usage statistics — and a switch to turn them off.** Settings →
   About now has a switch for anonymous usage statistics, and the first launch
   tells you what is collected before anything is sent. What is sent: the app
   version, your operating system, the interface language, and a random ID that
   changes every day. What is never sent: photos, file paths, or personal
   information.

7. **Check for a newer version from the About page.** The About page has its
   website link back, plus a button that looks up the current release when you
   ask it to. Nothing is checked in the background and nothing is downloaded or
   installed — it only reads the version number when you click.

8. **Dark menus no longer show white edges.** Drop-down lists throughout the app
   — filters, sorting, the bird ID country and region pickers, Settings — were
   drawn on top of the macOS light panel, leaving white strips above and below
   the list. Right-click menus in text fields carried icons drawn for a light
   theme, which were all but invisible on a dark menu.

9. **The app no longer hangs forever when an external tool stops responding.**
   Thirteen places that call out to external programs had no time limit, so one
   stuck call could freeze the app for good.

10. **Folders processed by older versions open again.** A results database
    written by an earlier version could be missing columns the browser expects;
    missing columns are now filled in on open.

11. **What the app tells you now matches what it does.** The star rules on the
    console and in step 2 of the usage guide describe the batch-quota system
    actually in use, the burst note quotes the minimum you configured instead of
    a hard-coded 4, and a few Chinese strings that leaked into the English
    interface are gone.

12. **Smaller fixes.** On macOS the app no longer leaves behind the helper that
    keeps your Mac awake after you quit; deleting files copes with unusual
    characters in filenames; the aesthetics threshold can go as low as the
    slider allows instead of snapping back; and cancelling an Apple Photos
    import now actually stops.

---

# SuperPicky 4.6.0（中文）

这一版的重点是把成果带出软件：导出一份可以直接发给别人的报告、把选出的照片送进
Apple 照片、以及一次改掉整个认错的鸟种。

## 这一版有什么新东西

1. **导出一份可以分享的拍摄报告。** 选鸟浏览器新增「导出报告」按钮，会在选鸟目录
   里生成一个 HTML 文件，双击就能打开，照片直接嵌在文件里——发给鸟友、发到群里，
   或者留着自己回顾都行。断网也能看，图片永远不会丢。报告开头是这次最好的一张，
   接着每个鸟种一块、按罕见度排序，带中文名、学名、罕见度标签，受威胁鸟种还有
   IUCN 标签，每种最多四张。同一组连拍只取一张，所以看到的是四个不同瞬间，而不是
   四张几乎一样的照片。每张都标着曝光参数，每种的代表作还会显示锐度、美学和鸟种
   颜值。下面是星级分布、命中率、飞版数、精焦数、连拍组数和器材统计。点任意一张
   可以放大。还有「存为 PDF」按钮，会转成白底适合打印。一次外拍的量——284 张照片、
   12 个鸟种——大约 4 MB。

2. **把选出的照片直接送进 Apple 照片（仅 macOS）。** 选鸟浏览器新增「添加到照片」
   按钮。有 RAW 就导入 RAW，并把鸟种名、你打的星级和质量数据写进照片的标题、描述
   和关键词，这样在「照片」里就能直接搜鸟种。已经送过去的会自动跳过，再点一次不会
   重复。勾选了缩略图就只导入勾选的，一张没勾就导入当前筛选出的全部。每次运行会
   按文件夹名和日期建一个相簿，收在「SuperPicky Imports」文件夹下。你的 RAW 文件
   不会被改动，XMP 边车也不会送进「照片」。由 @orientaldollarbird 贡献。

3. **一次改掉整个认错的鸟种。** 一批照片从头到尾认成同一种错鸟时，右键任意一张选
   「把整个「某某鸟」改为…」。它会把数据库里这个鸟种的**全部**照片改掉——不只是
   当前筛选出来的那些——并搬进新鸟种的文件夹，连拍组整组一起走。动手之前会先给你
   一份确认：涉及多少张、多少个连拍组、目标文件夹的确切名字，所以用英文整理过的
   目录不会悄悄多出一套中文文件夹。顺带修了一个老问题：改鸟种时如果目标文件夹里
   已有同名文件，以前会静默失败——数据库改了、文件却没动。现在文件和数据库不会再
   各说各话。

4. **4 星和 5 星有了自己的文件夹。** 手动升上去的照片不再和 3 星混在一起，键盘打星
   也放开到了 5 星。手动升的星现在也计入统计：命中率改成「3 星及以上」，升一张星
   不会再让命中率反而下降。

5. **「只看精选」变成独立开关，精选永远排在最前面。** 皇冠原来挤在星级筛选那一排
   里，看着像是往列表里加照片，实际上是把列表缩到只剩精选。现在它是那一排下面单独
   的一个勾选框。另外，精选是「3 星里又锐又好看」的交集，所以单按锐度或罕见度排序
   会把它们打散：实测十二张精选分别落在第 2、4、8……44 位，按罕见度排时最远的排到
   第 120 位。现在精选永远排在最前面，你选的排序在精选内部生效。按文件名排序不受
   影响，因为它的意义就是拍摄顺序。

6. **匿名使用统计，以及一个可以关掉它的开关。** 设置 →「关于」新增匿名使用统计
   开关，首次启动会在发送任何数据之前告诉你收集了什么。发送的是：软件版本、操作
   系统、界面语言，以及一个每天都会变的随机 ID。绝不发送：照片、文件路径、个人
   信息。

7. **可以在「关于」页查最新版本。** 「关于」页恢复了官网入口，并新增一个按钮，点了
   才去查当前发布版本。后台不做任何检查，也不下载、不安装——只有你点的时候才读一次
   版本号。

8. **深色界面的菜单不再露白边。** 全软件的下拉列表——筛选、排序、识鸟的国家和地区
   选择、设置——原本画在 macOS 的浅色面板上，列表上下会露出白条。文本框的右键菜单
   用的是浅色主题的图标，在深色菜单上几乎看不见。

9. **外部工具卡住时软件不会再永久无响应。** 十三处调用外部程序的地方没有超时限制，
   一次卡住就会让软件永远转圈。

10. **旧版本处理过的目录又能打开了。** 早期版本写的结果数据库可能缺少浏览器需要的
    列，现在打开时会自动补上。

11. **软件说的和它做的对上了。** 控制台和使用步骤第 2 步里的星级规则，现在描述的是
    实际在用的批内配额；连拍提示引用的是你自己设的最小张数，不再是写死的 4；漏进
    英文界面的几处中文也清掉了。

12. **一些小修复。** macOS 上退出软件后不会再留下那个让 Mac 保持唤醒的辅助进程；
    删除文件能正确处理文件名里的特殊字符；美学阈值可以调到滑块允许的最低值而不会
    弹回；取消 Apple 照片导入现在是真的会停下来。

---
