# SuperPicky 4.6.4 — RC history / RC 阶段增量记录

本文件归档 4.6.4 开发期各 RC 版本的**增量**说明（RC1 → RC12），仅供追溯。
面向用户的完整发布说明见仓库根目录 `ChangeLog.md`，其中已并入下列全部内容。

RC12 之后、正式版之前另有两项改动，只在正式版说明里出现：
- CUDA 13 安装包缺少 torchvision 编译扩展导致 `nms` 算子不存在（606ca8c8、f947ae6b），
  RC12 的 CUDA 13 包因此不可用；修复版经测试版 vtest-cu130-rc14 在 RTX 50 实机验证；
  分卷大小改为 1.4 GB（42540a4a），仍为一个 .exe + 两个 .bin；
- 中文界面显示国家一级 / 二级保护等级（86a504b6）。

This file archives the per-RC **incremental** notes from the 4.6.4 development
cycle (RC1 → RC12) for traceability only. The user-facing release notes live in
`ChangeLog.md` at the repository root, which already incorporates everything
below.

---

# SuperPicky 4.6.4 RC12

**What's new since RC11:**

- **New: CUDA 13 installer for RTX 50 series graphics cards.** The regular CUDA build has no GPU code
  for RTX 5050/5060/5070/5080/5090, so on those cards it falls back to the much slower CPU (since
  RC10). The new `FullCUDA13` installer uses PyTorch 2.14.1 + CUDA 13.0 and runs on the GPU from
  GTX 16 series up to RTX 50 series. It needs **NVIDIA driver 580 or newer**; with an older driver
  it falls back to CPU. This is the first CUDA 13 build and has not been tested on real GPUs yet,
  so reports are very welcome.
- **The CUDA 13 installer comes in three files.** GitHub limits each file to 2 GB and this installer
  is about 2.2 GB, so it is split into one `.exe` and two `.bin` files. Download all three into the
  same folder, don't rename them, then run the `.exe`.
- **New: copy photos to a folder from the results browser.** Right-click → **Copy to Folder…** copies
  the checked photos (or just the one you right-clicked) to a folder you choose, together with each
  photo's `.xmp` sidecar and paired camera JPEG, so ratings and species names come along. Existing
  files are never overwritten: a name clash is saved as "name (1)". Shows progress, can be cancelled,
  checks free space first, and offers to open the folder when done.
- **Photo count while writing ratings, RAW+JPEG.** With RAW+JPEG shooting the count still showed
  twice the number of photos, because the RAW and its JPEG were counted separately. A pair now
  counts as one photo. (#150)
- **Clearer GPU fallback note in the log.** When the graphics card isn't supported by the installed
  build, the log now names the installer that works for it (CUDA 13 for RTX 50, CUDA 11.8 for
  GTX 9/10 series).

**Which installer?**

| Your computer | Download |
|---|---|
| Mac (Apple silicon) | `SuperPicky_v4.6.4RC12_arm64_….dmg` |
| Windows, no NVIDIA card (or any PC) | `SuperPicky_Setup_Full_Win64_….exe` (about 765 MB, CPU) |
| Windows, NVIDIA GTX 9/10/16, RTX 20/30/40 | `SuperPicky_Setup_FullCUDA_Win64_….exe` (CUDA 11.8) |
| Windows, NVIDIA RTX 50 (driver 580+) | `SuperPicky_Setup_FullCUDA13_Win64_….exe` **plus** both `….bin` files (CUDA 13) |

The two CUDA installers replace each other; your settings are kept.

---

# SuperPicky 4.6.4 RC12（中文）

**RC11 以来的变化：**

- **新增：给 RTX 50 系列显卡的 CUDA 13 安装包。** 现有的 CUDA 版没有为 RTX 5050/5060/5070/5080/5090
  编译 GPU 代码，这些显卡从 RC10 起只能改用 CPU，速度慢很多。新的 `FullCUDA13` 安装包用 PyTorch 2.14.1 + CUDA 13.0，
  从 GTX 16 系列到 RTX 50 系列都能用 GPU。需要 **NVIDIA 驱动 580 或更新**，驱动太旧会改用 CPU。
  这是第一个 CUDA 13 版本，还没在真实显卡上测过，欢迎反馈。
- **CUDA 13 安装包分成三个文件。** GitHub 单个文件不能超过 2 GB，这个安装包约 2.2 GB，所以拆成一个 `.exe`
  和两个 `.bin`。三个都要下载、放在同一个文件夹、不要改名，然后运行 `.exe`。
- **新增：在结果浏览器里把照片复制到文件夹。** 右键 →「复制到文件夹…」，把勾选的照片（或右键点的那一张）复制到
  你选的文件夹，同名的 `.xmp` 边车和配套相机 JPEG 一起复制，星级和鸟名不会丢。绝不覆盖已有文件，重名的存成
  「文件名 (1)」。有进度、可取消、先检查剩余空间，完成后可直接打开文件夹。
- **RAW+JPEG 时写入评分的张数正确了。** RAW 和配套 JPEG 原来各算一张，显示成照片数的两倍；现在一组只算一张。（#150）
- **日志里的 GPU 回退说明更明确。** 显卡不被当前安装包支持时，日志会写明该换哪个安装包（RTX 50 用 CUDA 13 版，
  GTX 9/10 系列用 CUDA 11.8 版）。

**该下载哪个？**

| 你的电脑 | 下载 |
|---|---|
| Mac（Apple 芯片） | `SuperPicky_v4.6.4RC12_arm64_….dmg` |
| Windows，没有 N 卡（任何电脑都能用） | `SuperPicky_Setup_Full_Win64_….exe`（约 765 MB，CPU） |
| Windows，NVIDIA GTX 9/10/16、RTX 20/30/40 | `SuperPicky_Setup_FullCUDA_Win64_….exe`（CUDA 11.8） |
| Windows，NVIDIA RTX 50（驱动 580+） | `SuperPicky_Setup_FullCUDA13_Win64_….exe` **加上**两个 `….bin`（CUDA 13） |

两个 CUDA 安装包会互相覆盖，设置会保留。国内下载慢可以在附件链接前加 `https://gh-proxy.com/`。

---

# SuperPicky 4.6.4 RC11

**What's new since RC10:**

- **Open several photos in an external app at once.** **Open with …** in the right-click menu and
  Ctrl/Cmd+1…9 now open every checked photo, not just one. Right-clicking a checked photo opens the
  whole selection (the menu shows how many); right-clicking an unchecked photo opens only that one;
  fullscreen opens the current photo. Opening more than 20 at once asks first.
- **Star rating at the top of fullscreen is no longer stale.** After switching the rating filter to
  0 stars, or using Previous/Next onto a 0-star photo, the top bar kept showing the previous
  photo's stars. (#151)
- **"Other birds" in the Species list works.** It only appears when the current rating filter has
  such photos, so picking it no longer shows an empty grid and makes the item disappear. It is now
  labelled "Other birds" instead of the folder name "Other_Birds". (#148)
- **Correct photo count while writing ratings.** The last step of culling counted write jobs
  instead of photos, so the total could be much larger than the number of photos. It now counts photos and updates more often,
  instead of sitting at 0 on slower (e.g. external) drives. (#150)
- **Visible hover in drop-down lists.** The item under the mouse in drop-down lists (Species,
  Countries and others) is now highlighted in the accent colour, like the right-click menu. (#149)
- **No more freeze after changing species in bulk.** After the move step, writing the new species
  into the photos now runs in the background, with progress in the results browser's status bar
  ("Writing species to photos 12/40…"). (#147)

---

# SuperPicky 4.6.4 RC11（中文）

**RC10 以来的变化：**

- **外部应用可一次打开多张照片。** 右键「用 … 打开」和 Ctrl/Cmd+1…9 现在会打开全部勾选的照片，不再只开一张。
  右键点在勾选的照片上打开整个勾选集（菜单写明张数），点在未勾选的照片上只开这一张；全屏打开当前这张。
  一次超过 20 张会先确认。
- **全屏顶部星级不再残留。** 把评分筛选切到 0 星、或用上一张/下一张翻到 0 星照片时，顶部仍显示上一张的星级。（#151）
- **鸟种下拉的「其他鸟类」恢复正常。** 只在当前星级筛选下确有这类照片时才出现，选中后不再是空白、选项也不会消失；
  英文界面显示为「Other birds」，不再是目录名「Other_Birds」。（#148）
- **写入评分的张数正确了。** 选片最后一步原来数的是写入任务而不是照片，总数会明显多于照片张数；现在按张数显示，
  更新也更及时，外接盘上不会长时间停在 0。（#150）
- **下拉列表有悬停效果。** 鸟种、国家等下拉列表里鼠标所在的一项会用强调色高亮，与右键菜单一致。（#149）
- **批量改鸟种后界面不再卡住。** 移动文件之后，把新鸟名写入照片改在后台进行，结果浏览器底部状态栏显示进度
  （「正在把鸟名写入照片 12/40…」）。（#147）

---

# SuperPicky 4.6.4 RC10

**What's new since RC9:**

- **Correct Bird ID on RTX 50 series graphics cards.** With the CUDA build, RTX 5050/5060/5070/5080/5090
  cards ran without errors but identified the wrong species, because this build has no GPU code for
  them. SuperPicky now detects this and uses the CPU instead, so results are correct; the log says
  so in the session header ("GPU Fallback"). A CUDA build for RTX 50 cards will follow.

---

# SuperPicky 4.6.4 RC10（中文）

**RC9 以来的变化：**

- **RTX 50 系列显卡识鸟恢复正确。** CUDA 版在 RTX 5050/5060/5070/5080/5090 上不报错，但识别出的鸟种是错的——
  这个版本没有为这一代显卡编译 GPU 代码。现在会自动检测并改用 CPU，结果正确；日志开头会写明「GPU Fallback」。
  适配 RTX 50 的 CUDA 版随后提供。

---

# SuperPicky 4.6.4 RC9

**What's new since RC8:**

- **You can stop culling midway.** While culling runs, the **Start Culling** button becomes
  **Stop**. Photos already processed keep their ratings; next time you start, you can continue
  from where it stopped or restart. (#144)
- **No more silent wait at "100%".** The last steps — finishing Bird ID, assigning star
  ratings, writing ratings into the photos, organizing and cleaning up, and videos — now show
  what they are doing and how far along they are (e.g. "Writing ratings to photos 320/1200…").
  If ExifTool stops responding (often antivirus scanning the photo folder), SuperPicky now notices
  within 60 seconds instead of waiting over 5 minutes per batch, stops after two timeouts in a
  row and explains what to do. The log records how long writing ratings took.
- **Change the country right from the start dialog.** When Bird ID is on, the Start Culling
  confirmation has a **Change Country…** link; pick a country and the dialog comes back showing
  it. The current country is shown in bold. (#145)
- **Frequent countries you can edit.** The country list still starts with the usual countries,
  plus your own country (from your computer's region setting). A new country you pick in
  **More Countries** is added automatically, and **Edit…** next to the country list lets you
  remove countries or restore the defaults. **More Countries** now lists every country. (#143)
- **Reset now really clears the folder.** On Windows, three report files could be left behind
  after a reset, so the main window still offered **View Results**. Fixed; the button is now
  always called **Reset** (it no longer turns into "Reprocess"). (#146)
- **Burst note is accurate.** With the flat layout, or with burst folders off, the start dialog
  now says bursts are only grouped and no files are moved. (#145)
- **GWTE finds Green-winged Teal.** The 4-letter code now points to the North American bird
  (*Anas carolinensis*) instead of Eurasian Teal. (#140)
- **Clearer "Write to file" wording.** It no longer claims ratings are written into CR3 and
  other proprietary RAW files; the RAW option now sits under **Write to file**. (#139)

---

# SuperPicky 4.6.4 RC9（中文）

**RC8 以来的变化：**

- **选片中途可以停止。** 处理时「开始选片」按钮变成「停止」。已处理的照片保留评分；下次开始可以从停下的地方
  继续，或重新开始。（#144）
- **不再在「100%」干等。** 收尾几步——汇总识鸟、定星、把评分写入照片、整理与清理、视频——现在会显示在做什么、
  做到哪（例如「正在把评分写入照片 320/1200…」）。ExifTool 没有响应时（常见是杀毒软件在扫描照片文件夹），
  60 秒内就会发现，不再每批干等 5 分多钟；连续两次超时就停止并说明怎么办。日志里会记下写入评分用了多久。
- **开始前可以直接换国家。** 开着识鸟时，开始选片的确认框里有「更换国家…」链接，选好国家会回到确认框并显示
  新国家；当前国家加粗显示。（#145）
- **常用国家可以编辑。** 国家列表仍以常用国家开头，你所在的国家（按电脑的地区设置）也会在里面。在「更多国家」
  里选的新国家会自动加进来；国家列表旁的「编辑…」可以移除或恢复默认。「更多国家」现在列出全部国家。（#143）
- **重置会真正清干净。** Windows 上重置后可能残留 3 个报告文件，主界面仍显示「查看结果」。已修复；按钮统一叫
  「重置」，不再变成「重新处理」。（#146）
- **连拍说明更准确。** 输出方式为平铺、或关闭「连拍归入文件夹」时，确认框说明只识别连拍分组、不移动文件。（#145）
- **GWTE 能找到美洲绿翅鸭。** 4 位代码改为指向北美的绿翅鸭（*Anas carolinensis*），不再是欧亚绿翅鸭。（#140）
- **「写入文件」说明更清楚。** 不再声称评分会写进 CR3 等专有 RAW 文件；RAW 选项挪到「写入文件」下。（#139）

---

# SuperPicky 4.6.4 RC8

**What's new since RC7:**

- **BirdTrace is now the only bird-ID model.** The previous OSEA model, compare mode, the
  Settings → Bird ID → **Bird ID model** choice and the results browser's **"Disagreements
  only"** filter are gone. In our hand-checked batches BirdTrace was clearly more accurate
  (Victoria 97% vs 89.7%), so a second opinion from the weaker model wasn't worth the extra
  time. If you had picked a model in RC7, that setting is simply dropped. Lightroom captions
  no longer carry the OSEA check line.
- **Better framing for bird ID during culling.** Batch culling used to send BirdTrace a
  rectangular crop squashed into a square, which stretched the bird. It now sends a square crop
  around the bird with the real background, the same framing the Bird ID panel and BirdTrace's
  author use. On the Victoria test batch: 97.1% instead of 96.0%, and fewer confident mistakes.
- Installers are about 100 MB smaller than RC7 (the OSEA model is no longer bundled), and
  culling with bird ID is back to about the speed of BirdTrace alone in RC7.
- The command-line `--model` and `--tta` options were removed along with OSEA.

---

# SuperPicky 4.6.4 RC8（中文）

**RC7 以来的变化：**

- **识鸟只用 BirdTrace。** 原来的 OSEA 模型、对比模式、设置 → 识鸟 → **识鸟模型** 选项，以及结果
  浏览器的 **「只看分歧」** 筛选都已删除。在我们人工核对过的批次里 BirdTrace 明显更准（维州 97% 对
  89.7%），用较弱的模型再验证一遍不值得多花的时间。RC7 里选过的识鸟模型设置会自动丢弃。Lightroom
  题注里不再有 OSEA 验证那一行。
- **选片时识鸟的取图更准。** 以前批量选片送给 BirdTrace 的是长方形裁剪，被硬压成正方形，鸟会被拉伸
  变形。现在改送以鸟为中心、带真实背景的正方形裁剪，和识鸟面板、BirdTrace 作者的做法一致。维州测试
  批次：准确率从 96.0% 提到 97.1%，置信度高却认错的情况也更少。
- 安装包比 RC7 小约 100 MB（不再带 OSEA 模型）；开着识鸟选片的速度回到 RC7 里只用 BirdTrace 的水平。
- 命令行随 OSEA 一起去掉了 `--model` 和 `--tta` 参数。

---

# SuperPicky 4.6.4 RC7

**What's new since RC6:**

- **New bird-ID model: BirdTrace** (FastViT-MA36, about 10,000 species, by Warden,
  [Hugging Face](https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K), CC BY-NC 4.0).
  In our hand-checked test batches it was clearly more accurate than the previous OSEA model
  (Victoria 97.4% vs 89.7%), and it knows recently split species that OSEA never learned
  (Opalton Grasswren: 98% vs 0%).
- **Compare mode is the default in this test build.** BirdTrace identifies the bird and OSEA
  checks it. The Lightroom caption shows BirdTrace's confidence plus an extra line saying
  whether OSEA agrees. Settings → Bird ID → **Bird ID model** lets you switch to BirdTrace
  only or OSEA only. Every compared photo is also logged to `birdid_engine_compare.csv` in
  the SuperPicky user-data folder.
- **New "Disagreements only" filter** in the results browser. It appears only when the folder
  has compare results, and shows the photos where OSEA's check differs from the current
  species (it follows your species edits). Please tell us which model was right on those photos.
- **Region filter never widens to the whole world.** If a photo matches nothing in your
  state's eBird list, SuperPicky still widens to the country list, but it no longer drops the
  filter altogether. A photo that matches nothing in the country list now gets no species
  instead of a bird from another continent. (In a South Australia test batch, 12 of 1,784
  identifications had fallen back to worldwide and produced Oriental Magpie-Robin, Ashy
  Woodswallow, Vermilion Flycatcher and similar.) Without GPS or a chosen region, nothing
  changes: there is no list to filter by.
- **Chinese UI: English name under the Chinese bird name** on thumbnail cards and in the
  detail panel, so similar Chinese names can't be confused.
- **Fixed: Lightroom still read 3★ after promoting a photo to 4★ / 5★.** The rating is now
  written after the file has been moved, not at the same time.
- The aesthetic model's weights are now stored at half precision so the Windows CUDA
  installer stays under GitHub's 2 GiB file limit. On Apple-silicon and NVIDIA GPUs the
  scores are bit-identical (the model already ran at half precision there); CPU-only scores
  differ by at most about 0.004.
- Installers are about 200 MB larger (the BirdTrace model). On a Mac, processing is about 7%
  slower with BirdTrace alone and about 14% slower in compare mode. **Speed on Windows
  without an NVIDIA GPU has not been measured yet — please report it.**

---

# SuperPicky 4.6.4 RC7（中文）

**RC6 以来的变化：**

- **新识鸟模型 BirdTrace**（FastViT-MA36，约 1 万种，作者 Warden，
  [Hugging Face](https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K)，CC BY-NC 4.0）。
  在我们人工核对过的测试批次里明显比原来的 OSEA 准（维州 97.4% 对 89.7%），并且认得 OSEA
  没学过的新拆分物种（锈色草鹩莺 98% 对 0%）。
- **测试版默认「对比模式」**：BirdTrace 识别、OSEA 验证。Lightroom 题注里的置信度来自
  BirdTrace，另有一行说明 OSEA 是否同意。设置 → 识鸟 → **识鸟模型** 可改成只用 BirdTrace
  或只用 OSEA。每张对比过的照片还会记录到用户数据目录的 `birdid_engine_compare.csv`。
- **结果浏览器新增「只看分歧」筛选**：文件夹里有对比结果时才出现，只显示 OSEA 验证结果与当前鸟种不同的
  照片（以当前鸟种为准，手动改鸟种后随之更新）。欢迎告诉我们这些照片上哪个模型是对的。
- **地区过滤不再放宽到全世界。** 照片在省州 eBird 名录里找不到对应鸟种时，仍会放宽到国家名录，
  但不再完全取消过滤；国家名录里也没有的照片现在不定鸟种，而不是给出别的大洲的鸟。（南澳一批
  测试照片的 1784 次识别里，有 12 次放宽到全世界，出现了鹊鸲、灰燕鵙、猩红霸鹟等外地鸟。）没有
  GPS、也没手选地区时行为不变：本来就没有名录可用。
- **中文界面在鸟名下换行显示英文名**（缩略图卡片和详情面板），避免相近的中文名混淆。
- **修复：照片升到 4★ / 5★ 后 Lightroom 仍读到 3★。** 现在先移动文件、再写星级。
- 美学模型权重改为半精度存储，让 Windows CUDA 安装包保持在 GitHub 单文件 2 GiB 上限以内。
  在 Apple 芯片和 NVIDIA 显卡上分数逐位不变（那里本来就用半精度计算）；纯 CPU 计算最多差约
  0.004 分。
- 安装包约大 200 MB（BirdTrace 模型）。Mac 上只用 BirdTrace 处理约慢 7%，对比模式约慢 14%。
  **没有 NVIDIA 显卡的 Windows 电脑速度还没测过，欢迎反馈。**

---

# SuperPicky 4.6.4 RC6

**What's new since RC5:**

- **Rating filter: All / None buttons** (#137). The Rating Filter heading in the results
  browser now has **All** and **None** on the right. The chips now follow one rule: a lit
  chip is shown, an unlit chip is hidden. **Turning every chip off now shows no photos**
  (it used to show everything), so *None* followed by one chip is the quickest way to look
  at a single rating. When a folder opens and the default 3★ + 2★ filter finds nothing,
  every chip is lit instead of cleared.
- **Previous / Next are greyed out at either end** (#137), in the side panel and in the
  full-screen viewer. In a burst opened in full screen, the ends are the burst's first and
  last shot.
- **New "Rating" sort** (#137): highest rating first, and within each rating picked photos
  first, then the sharpest. Hand-promoted 4★ / 5★ shots come straight to the top. (4★ and
  5★ are manual-only tiers; the 3★ chip includes them.)
- **Fixed: common birds filtered out by the region list.** The eBird regional lists use
  the 2025 taxonomy, and species from recent splits (Little Heron, Sahul Sunbird, Mongolian
  Gull …) had no matching class in the model, so they were dropped from the list. Birds
  such as Striated Heron, Olive-backed Sunbird and Northern Goshawk could be identified
  correctly and then filtered out in Australia and China. 34 split species are now mapped
  back to their pre-split class (Australia 927 → 933 species, Queensland 699 → 705,
  China 1402 → 1412).

---

# SuperPicky 4.6.4 RC6（中文）

**RC5 以来的变化：**

- **评分筛选加「全选 / 全不选」**（#137）。结果浏览器「评分筛选」标题右侧新增「全选」「全不选」。
  星级筹码改为统一规则：亮着的显示，熄灭的不显示。**筹码全部熄灭时现在一张都不显示**（以前是
  全部显示），所以「全不选」再点亮某一档，就是只看这一档最快的方法。打开文件夹时如果默认的
  3★ + 2★ 筛不出照片，改为点亮全部筹码（以前是全部清空）。
- **「上一张 / 下一张」到头时置灰**（#137），详情面板和全屏查看器都是。全屏查看连拍组时，
  首尾指的是这一组的第一张和最后一张。
- **新增「星级」排序**（#137）：星级从高到低，同一星级内精选在前，其余按锐度从高到低。
  手动升到 4★ / 5★ 的照片直接排在最前。（4★ 和 5★ 只能手动打，3★ 筹码包含它们。）
- **修复：常见鸟被地区名录过滤掉。** eBird 地区名录用的是 2025 分类，近年新拆分出的物种
  （Little Heron、Sahul Sunbird、Mongolian Gull……）在模型里没有对应类别，生成名录时被丢弃，
  导致绿鹭、黄腹花蜜鸟、苍鹰等常见鸟在澳洲、中国即使识别对了也会被过滤掉。现已把 34 个拆分
  物种对回拆分前的模型类别（澳洲 927 → 933 种、昆士兰 699 → 705、中国 1402 → 1412）。

---

# SuperPicky 4.6.4 RC5

**What's new since RC4:**

- **Choose which file formats to process** (#126). **Settings → Culling → File formats
  to process** can be switched from *All supported formats* to *Only the formats checked
  below*, with one checkbox per camera brand (Canon, Nikon, Sony, Fujifilm, OM System /
  Olympus, Panasonic, Pentax, Hasselblad, Phase One) plus DNG, HEIF and *Standalone
  JPEG*. A JPEG with the same name as a RAW is part of that RAW photo and follows it;
  *Standalone JPEG* (a JPEG without a RAW of the same name) is unchecked by default.
  Files that are not processed stay where they are. Because the setting applies to
  every folder until you change it, the start-of-processing dialog says when it is on,
  and the log lists how many files of each format were skipped.
- **Fixed: a RAW with the same name as another RAW could replace the original.** With
  both `IMG_1234.CR3` and a `IMG_1234.dng` exported by Topaz in the same folder, only one
  was processed, and which one depended on the file system — often the DNG. The CR3
  was then left unrated, and its .xmp sidecar moved away with the DNG. Now the camera
  RAW is always the one processed (or the DNG, if you only check DNG in the new format
  setting); the other file stays where it is and is listed in the log.

---

# SuperPicky 4.6.4 RC5（中文）

**RC4 以来的变化：**

- **可以选择只处理哪些文件格式**（#126）。**设置 → 精选 → 处理的文件格式**可以从
  「全部支持的格式」改成「只处理下面勾选的格式」，按相机品牌勾选（Canon、Nikon、Sony、
  Fujifilm、OM System / Olympus、Panasonic、Pentax、Hasselblad、Phase One），另有
  DNG、HEIF 和「单独的 JPEG」。与 RAW 同名的 JPEG 算作那张 RAW 的一部分，跟随它处理；
  「单独的 JPEG」（没有同名 RAW 的 JPEG）默认不勾。不处理的文件原地保留。这个设置对
  之后处理的每个文件夹都生效，所以开启时开始处理前的确认框会提示，日志里也会列出各格式
  跳过了多少个文件。
- **修复：与另一个 RAW 同名的 RAW 可能顶替原片。** 同一文件夹里同时有 `IMG_1234.CR3`
  和 Topaz 导出的 `IMG_1234.dng` 时，只会处理其中一个，而且是哪个取决于文件系统——常常
  是 DNG。于是 CR3 原片没有评分，它的 .xmp 边车还被 DNG 带走。现在总是处理相机原生 RAW
  （在新的格式设置里只勾 DNG 时则处理 DNG），另一个文件原地保留并在日志中列出。

---

# SuperPicky 4.6.4 RC4

**What's new since RC3:**

- **Fixed: a folder with DNG files exported by Topaz stopped the whole run** (#125). A RAW
  file with no readable preview image (for example a linear DNG from Topaz, which has no
  embedded preview) made processing fail with an error. Such files are now skipped and
  left where they are, the rest of the folder is processed normally, and the log lists
  the skipped files at the end of the preview stage. The message now follows the
  interface language.
- **Deleting in the results browser** (#127 #129 #130 #131 #132 #133):
  - Press **Delete** in the grid to delete the selected photos (⌘/Ctrl+Backspace still
    works).
  - The right-click menu has a new **Delete…** item. If the photo you right-click is one
    of the checked photos, all checked photos are deleted; otherwise just that photo.
    The confirmation dialog with *Don't ask again* is unchanged.
  - Deleting a photo now also moves its .xmp sidecar and, for RAW+JPEG shots, the camera
    JPEG to the Trash / Recycle Bin, and removes its cached previews inside the hidden
    .superpicky folder.
  - Fixed: after deleting in the full-size view, the deleted photo could come back when
    using the arrow keys, and deleting it again showed an error.
  - Fixed: the grid kept an empty gap where a deleted photo had been until the window
    was resized.
  - Fixed: with one photo checked, a keyboard delete could also delete the unchecked
    photo kept for side-by-side comparison.
- **Fixed: arrow keys in the full-size view did not update the crop / YOLO image** in the
  side panel (#127).
- **Open with an external app from the keyboard** (#128): **⌘1…⌘9** (Ctrl+1…Ctrl+9 on
  Windows) opens the current photo with the 1st…9th app in **Settings → External Apps**.
  The right-click menu shows the shortcut next to each *Open with* item. Previously
  Ctrl+number set a star rating by mistake.
- **The HTML report** title now starts with *SuperPicky* and the report has the
  SuperPicky icon in the browser tab (#134).

---

# SuperPicky 4.6.4 RC4（中文）

**RC3 以来的变化：**

- **修复：文件夹里有 Topaz 导出的 DNG 时整批处理报错中止**（#125）。读不到预览图的
  RAW（例如 Topaz 导出的线性 DNG 不带内嵌预览）会让处理直接报错停止。现在这类文件会被
  跳过并原地保留，其余照片照常处理，预览提取阶段结束时日志里列出被跳过的文件；提示
  文字也改为跟随界面语言。
- **结果浏览器里的删除**（#127 #129 #130 #131 #132 #133）：
  - 网格里选中照片后按 **Delete** 键即可删除（⌘/Ctrl+Backspace 仍然可用）。
  - 右键菜单新增 **删除…**：右键点中的照片在勾选里，就删除所有勾选的照片；否则只删
    这一张。删除确认框（含「以后不再询问」）保持不变。
  - 删除照片时，同名 .xmp 边车以及 RAW+JPEG 拍摄的相机 JPEG 会一起移入回收站，
    隐藏文件夹 .superpicky 里这张照片的缓存图也会删掉。
  - 修复：在大图模式下删除后，用方向键还能翻回被删的照片，再删一次会报错。
  - 修复：删除后网格里留下一个空格，要调整窗口大小才会消失。
  - 修复：只勾选一张时用键盘删除，可能把双图对比用的另一张（未勾选）也一起删掉。
- **修复：大图模式下用方向键翻页，右侧的裁切 / YOLO 图不跟着更新**（#127）。
- **用快捷键调用外部应用打开**（#128）：**⌘1…⌘9**（Windows 为 Ctrl+1…Ctrl+9）用
  **设置 → 外部应用**里第 1…9 个应用打开当前照片，右键菜单的「用 XX 打开」旁边会显示
  对应快捷键。此前按 Ctrl+数字会被误当成打星。
- **HTML 报告**的标题改为以 *SuperPicky* 开头，浏览器标签页上也会显示慧眼选鸟图标（#134）。

---

# SuperPicky 4.6.4 RC3

**What's new since RC2:**

- **Closing the window now asks what you want** (#123). Until now, closing the main
  window always kept SuperPicky running in the system tray (so the Lightroom bird ID
  plugin keeps working), and on Windows it was easy to miss that it was still running.
  The first time you close it, SuperPicky now asks whether to keep running in the tray
  or quit completely, and remembers your choice. You can change it any time in
  **Settings → External Apps → When closing the main window**.
- **Only one copy of SuperPicky runs at a time** (#123). Launching it again brings the
  existing window to the front (even when it is hidden in the tray) instead of starting
  a second copy that loads every model again.
- **Search species by their eBird and IOC English names** (#122). The species list keeps
  its current names, but *Edit Species* and the bird name search now also find a species
  by its eBird name and US spelling, for example *American Pipit* (listed as Buff-bellied
  Pipit) or *Gray Catbird* (listed as Grey Catbird). About 2,300 species gain an
  English search alias.
- **Fixed: the Bird ID country could be saved as "── More Countries ──"** (#122). In
  **Settings → Bird ID → Country / Region**, cancelling the *More Countries* list, or
  pressing Enter after typing a country name without clicking it, left that entry
  selected, and it was then saved as your country, so photos without GPS were identified
  without a regional filter. Cancelling now keeps your previous country, and typing a
  country name and pressing Enter selects it when only one country matches. If the
  start-of-processing dialog shows "── More Countries ──" as your country, please pick
  your country again.
- **Fixed: dialogs said files would be moved when *Organize by* is *Flat*** (#120). The
  start-of-processing dialog, the *Edit Species* dialog and the species merge
  confirmation now say that files stay where they are. For the folder layouts, the
  start-of-processing dialog now lists the actual folder names for your layout.
- **Fixed: in English and Traditional Chinese, low-rated photos could end up in a
  separate Simplified Chinese "其他鸟类" folder.** Changing a rating, the species, or
  marking a photo as no bird in the results browser moved it into a new 其他鸟类
  folder instead of the *Other_Birds* folder created during processing.
- **Rarity corrected for a few recently split species** that were wrongly shown as
  *Legendary*: Western Whistler, Brush Cuckoo, Arafura Shrikethrush, Japanese Tit and
  Great Tit are now rated *Common*.

---

# SuperPicky 4.6.4 RC3（中文）

**RC2 以来的变化：**

- **关闭窗口时会先问你怎么处理**（#123）。之前关闭主窗口一律让慧眼选鸟留在系统托盘
  继续运行（这样 Lightroom 识鸟插件才能用），在 Windows 上很容易以为已经退出了。现在
  第一次关闭时会询问「留在托盘继续运行」还是「完全退出」，并记住你的选择；之后可在
  **设置 → 外部应用 → 关闭主窗口时**随时修改。
- **同一时间只运行一个慧眼选鸟**（#123）。再次启动时会把已经在运行的窗口调到前面
  （包括藏在托盘里的情况），不再另起一个重新加载全部模型的进程。
- **鸟种搜索支持 eBird 与 IOC 英文名**（#122）。鸟种列表的名字不变，但「修改鸟种」
  和鸟名查询现在也能用 eBird 叫法和美式拼写找到鸟种，例如 *American Pipit*（列表里叫
  Buff-bellied Pipit，黄腹鹨）、*Gray Catbird*（列表里叫 Grey Catbird）。约 2300 个
  鸟种因此多了英文搜索名。
- **修复：识鸟国家可能被存成「── 更多国家 ──」**（#122）。在**设置 → 识鸟 → 国家 /
  地区**里，打开「更多国家」列表后取消，或者输入国家名后没点选就直接回车，下拉框会
  停在这一项上，随后被当成国家保存——没有 GPS 的照片因此不做地区过滤。现在取消会保留
  原来的国家；输入国家名后若只剩一个匹配，回车即选中。如果开始处理前的弹窗里国家显示
  为「── 更多国家 ──」，请重新选一次国家。
- **修复：「组织方式」选「平铺」时，弹窗仍说会移动文件**（#120）。开始处理前的说明、
  「修改鸟种」弹窗和整种合并确认框，现在都会说明文件留在原处。其他组织方式下，开始
  处理前的说明会列出实际会用到的文件夹名。
- **修复：英文和繁体界面下，低星照片可能被移进一个单独的简体「其他鸟类」文件夹。**
  在结果浏览器里改星级、改鸟种或标记为无鸟时，照片会被移进新建的「其他鸟类」，而不是
  处理时建好的 *Other_Birds*（繁体为「其他鳥類」）。
- **修正几个新拆分鸟种的罕见度**：西方啸鹟、灌丛杜鹃、棕鵙鹟、大山雀和欧亚大山雀
  此前被误判为「传奇」，现在为「常见」。

---

# SuperPicky 4.6.4 RC2

**What's new since RC1:**

- **Fixed: with *Organize by: Flat*, actions on RAW photos hit a hidden preview
  instead of the RAW** (#118). SuperPicky recorded the internal preview JPG (inside
  `.superpicky/cache`) as each RAW photo's location, so in the results browser *Open
  with…*, *Copy Path* and *Show in Finder/Explorer* pointed at that JPG, a star
  rating you changed was written to the preview rather than the RAW (Lightroom never
  saw it), and deleting a photo moved the preview to the trash and left the RAW in
  place. Folders you have already processed are corrected automatically when you
  open them; there is no need to process them again. The other folder layouts were
  not affected.

---

# SuperPicky 4.6.4 RC2（中文）

**RC1 以来的变化：**

- **修复：「整理方式」选「平铺」时，对 RAW 照片的操作作用到了隐藏的预览图上，而不是
  RAW 本身**（#118）。SuperPicky 把内部的预览 JPG（在 `.superpicky/cache` 里）记成了
  RAW 照片的位置，于是在结果浏览器里「用外部应用打开」「复制路径」「在访达 / 资源
  管理器中显示」指向的都是那张 JPG；改的星级写进了预览图而不是 RAW（Lightroom 看
  不到）；删除照片时进回收站的是预览图，RAW 原地不动。
  之前处理过的目录，打开时会自动纠正，不必重新处理。其他整理方式不受影响。

---

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
