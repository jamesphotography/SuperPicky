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
