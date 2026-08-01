# SuperPicky 4.5.0

This release changes how stars are given out, adds a way to cull without moving
your files, and rebuilds the data behind bird identification. It is also about
30% faster.

---

## Stars now compare photos within the same batch

Before, a photo got 3 stars by clearing fixed sharpness and beauty scores. The
problem: on a bad-light morning nothing cleared the bar, and on a great morning
half the folder did.

Now SuperPicky ranks the photos in the folder against each other and hands out
stars by proportion. You decide the proportion with a single bar that splits the
batch into 3-star / 2-star / 1-star — drag its two dividers and the three shares
always add up to 100%.

The presets are: Beginner keeps 40% as 3-star, Intermediate 30%, Master 20%. The
2-star share is 30% in all three.

Two things worth knowing:

- **These are ceilings, not targets.** A photo still has to be sharp enough, and
  still gets capped at 2 stars if the eye isn't clearly visible. So a batch
  usually ends up below the number you set. That is normal.
- **With Bird ID on, the proportion applies per species.** Every species keeps
  its own best shots, and a rare bird keeps at least one. A hundred shots of the
  same common bird can no longer crowd out everything else.

Other rating improvements:

- Beauty scoring now looks at the bird, not the whole frame — a plain background
  no longer drags a good bird photo down.
- **Distant birds are no longer over-rated for sharpness.** The old measurement
  quietly favoured small birds: the same bird, further away, would score higher.
  That bias is now removed. One side effect — for birds that are small in the
  frame, the sharpness number you see will be lower than in older versions. Your
  thresholds still mean the same thing; only those inflated numbers changed.
- Stars are assigned once, at the end of the run. During processing you see
  measurements only, so ratings no longer jump around while you wait.
- The reason shown for each photo now reads "rank 30%" instead of "top 30%",
  which used to produce nonsense like "top 84%" on a 1-star photo.
- The old fixed-threshold method is still there if you prefer it. The switch sits
  right under the threshold heading in Settings, and swaps the controls below it.

## Cull without moving your files

Settings, Output, Folder layout now has a third choice: **Flat**.

Photos get detected, rated and tagged exactly as usual — star ratings, keywords,
picks, sidecar files for Sony RAW — but nothing is moved. Your Lightroom folders
keep working, because none of the paths change.

Browsing and filtering by stars, species, focus or burst works the same as
always. SuperPicky reads its own database, not your folder structure.

There is also a separate switch for burst photos: you can keep burst detection
(grouping in the browser, the per-burst limit on 3-star photos) without having
your bursts filed into `burst_001` style subfolders.

And the photo chosen to represent a burst group is picked better now — it weighs
focus and eye clarity, not just head sharpness.

## Colour labels changed — please read

The default colour labels are now the intuitive way round, green for good and red
for bad:

| Photo | Old label | New label |
|---|---|---|
| Bird in flight | Green | **Blue** |
| Critical focus | Red | **Green** |
| Soft / out of focus | (none) | **Red** |
| Good focus, or no bird | (none) | (none) |

Each photo gets one label, and flight wins over the others.

**If you built Lightroom smart collections on "green means flying", change them
to blue.**

## Bird names go into your keywords

When Bird ID is confident, the species name is now written to the photo's
standard keywords as well as the title. You can filter by species in Lightroom's
keyword panel.

Your own keywords are never touched, and running the same folder again does not
create duplicates. Switch: Settings, Bird ID, "Write species to photo keywords".

## Bird identification: the location data was rebuilt

SuperPicky narrows down species by where the photo was taken. The data behind
that has been replaced entirely, because the old source had two real problems:

- **Introduced birds were invisible.** The old data only covered where a species
  originally came from. In Sydney that meant the house sparrow, feral pigeon,
  common starling and common blackbird could never be identified — no matter how
  sharp the photo.
- **391 species were unreachable anywhere on earth**, simply missing from the
  data.

Both problems are gone. The new data is built from global bird observation
records and covers 233 countries.

Two more fixes in the same area:

- **Sparse regions no longer break the filter.** In a place with few recorded
  species, the old filter gave up and stopped filtering at all — which is how
  Iceland photos ended up identified as Little Penguin and Blue-footed Booby. It
  now widens its search step by step instead. On a 433-photo Iceland and Faroe
  set, all four wrong-hemisphere errors are gone and the identification rate went
  *up*.
- **Every country can be selected now.** The country list is generated from the
  same data the filter uses. Previously 11 countries in the list had no data
  behind them (selecting them did nothing at all), and 14 countries that did have
  data were missing from the list. Iceland and the Faroe Islands are among the
  ones you can now pick.

Note that a manually chosen country only applies to photos with no GPS.

Also fixed: when you ran SuperPicky from the command line with a relative folder
path, GPS was silently ignored — and with it all location filtering, without any
error message.

## About 30% faster

Measured on a real batch: 495 Sony RAW files, 135 seconds down to 95.

What changed: metadata for RAW files is now written to sidecar files instead of
rewriting the RAW itself; a bug that rewrote every cached preview on every photo
was fixed; and the metadata tool now runs as two processes instead of one, so
writing no longer blocks reading. That last one removed a 3-second stall that hit
roughly every 30 photos.

Full-screen browsing is also faster and much lighter on memory. Holding an arrow
key now runs entirely off cached previews, and on large libraries SuperPicky uses
about 2.4 GB less memory than before.

## More birds get found

When the first pass finds no bird at all, SuperPicky now takes a second look at
higher detail and uses the Bird ID model to confirm what it finds. This recovers
birds that were missed for being small, distant, or mistaken for an aeroplane or
a kite — without letting false positives through. Switch: Settings, Picking.

Also, when the camera's focus point says a shot is soft but the measured
sharpness of the bird's head is clearly fine, the measurement now wins. Fewer
genuinely sharp photos get pushed down.

## Bird ID and the Lightroom plugin

- **A species beauty index.** Every species now has a beauty score, shown in the
  detail panel and usable for filtering and sorting. It describes the *species*,
  not your photo, and has no effect on star ratings.
- **Fixing a wrong species.** Right-click a photo and choose "Edit Species" to
  correct an identification, or name a photo that has none yet.
- Closing the main window now keeps SuperPicky running in the tray, which fixes
  the Lightroom plugin being unable to reach it.
- The plugin's "write bird name" and "write caption" had been silently broken.
  Both work now, including Chinese text, which could previously come back as
  garbled characters.
- The Bird ID service starts within seconds instead of making the plugin wait
  ten seconds or more.
- Photos taken exactly on the equator or the prime meridian are no longer treated
  as having no GPS.
- On Windows, no more console windows flashing while reading photo metadata.

## All settings in one place

Every settings dialog has been merged into a single Settings window with
categories down the left: Picking, Bird ID, Output, External Apps, About. The
quick panel on the home page stays in sync with it, both ways.

New settings in this release:

- How bird names are displayed.
- Whether to confirm before deleting a photo.
- Clear all preview caches — removes the current folder's cached previews and
  crops, and never touches your original photos.

Skill level and thresholds are linked: edit a threshold by hand and it switches
to Custom. Shortcut to open Settings: Cmd+, on Mac, Ctrl+, on Windows.

Also fixed: radio buttons were invisible in Windows dark mode, and the Settings
window showed a grey patch in light mode.

## Results browser

- Change a photo's species or star rating in the browser and the files move to
  the matching folder automatically.
- Press 0 to 3 to set stars directly; Up and Down move a rating by one. Works in
  grid and full-screen view.
- "Picked" is now a lasting flag, with a crown badge on the thumbnail.
- Thumbnail labels are a single clean line — the species name, or the filename
  when there is no species yet. Hover to see the filename.
- The detail panel has a species row you can click to copy the name.
- Focus wording is now consistent on both sides of the window: Critical Focus,
  Good Focus, Soft.
- New icons throughout, and a Photoshop-style toolbar on the left in full-screen
  view.

## Reset

Reset now ignores hidden system files, and no longer leaves photos behind inside
burst folders. It finishes with a pass that flattens everything, as a safety net.

## A simpler interface

To keep SuperPicky focused on culling, several side features have had their menu
entries removed: in-app update checks, smart enhance, crop suggestions, video
analysis, and correction submission.

If you used Video Bird Analysis in 4.3.0, its menu is deliberately gone in 4.5.0.
This is not a bug. The code is still there and it may come back later.

## Windows: the Lite installer is gone

Lite had a small installer and downloaded the AI engine and models on first
launch. In practice that made the *total* download bigger, not smaller — about
1.2 GB in two stages against 792 MB for the full version in one. Worse, the
second stage depended on other servers being reachable, which is exactly where it
kept failing for users in China.

Windows now has two builds: CPU and CUDA, both complete and ready to run offline.

**If you currently use Lite, uninstall it before installing the full version.**
They use the same folder but count as different programs, so installing over the
top leaves you with two SuperPicky entries in Add or Remove Programs. Once
uninstalled, you can also delete the AI engine Lite downloaded — the full version
brings its own and will never look at it. That frees several GB.

The install is also about 68 MB smaller, thanks to the new bird location data
replacing a much larger file.

---

## Which download do I want

- **Mac with Apple Silicon** (M1 and newer): the standard installer.
- **Mac with Intel** (roughly pre-2020): the Intel installer. It runs on the CPU.
  It is built separately, so it may appear on the release page a little later.
- **Windows without an NVIDIA graphics card**: CPU version, on the release page.
- **Windows with an NVIDIA graphics card**: CUDA version. It is too large for
  GitHub, so it is on the file-sharing links instead.

There is no Lite installer in 4.5.0.

---

# SuperPicky 4.5.0（中文）

这一版改了星级的评判方式，新增了不移动文件的选片模式，并重建了鸟种识别背后的
数据。整体速度也快了约 30%。

---

## 星级改成在同一批照片里比较

以前一张照片能拿 3 星，靠的是锐度和美学分超过固定分数线。问题是：光线差的那
个早上，一张都过不了线；光线好的时候，半个文件夹都过线。

现在 SuperPicky 会把这个文件夹里的照片互相排名，按比例发星级。比例由一根配额
条决定，它把整批照片分成 3 星 / 2 星 / 1 星三段，拖动两个分隔点即可，三段占比
永远合计 100%。

预设是：新手档 3 星占 40%，进阶档 30%，大师档 20%。2 星占比三档都是 30%。

两点需要知道：

- **这是上限，不是目标。** 照片仍然必须够清晰，眼睛看不清仍然最多 2 星。所以
  实际结果通常低于你设定的比例，这是正常的。
- **开了识鸟，比例按鸟种分别执行。** 每个鸟种都保住自己最好的照片，罕见鸟至少
  留一张。一百张同一只常见鸟，再也挤不掉别的鸟种。

评星方面的其他改进：

- 美学评分改成只看鸟，不看整个画面——背景平淡不会再拖累一张好的鸟照。
- **远处的小鸟不再被高估锐度。** 旧的测量方式暗中偏袒小鸟：同一只鸟拍得更远，
  分数反而更高。这个偏差已经消除。副作用是：画面里占比小的鸟，你看到的锐度数
  值会比旧版本低。你的阈值含义没变，变的只是那些虚高的数字。
- 星级在处理结束时一次性分配。处理过程中只显示测量值，星级不会再跳来跳去。
- 每张照片的评星理由现在写「排名 30%」而不是「前 30%」，后者在 1 星照片上会
  出现「前 84%」这种说不通的话。
- 旧的固定分数线方式仍然保留。开关就在设置里阈值标题的正下方，切换后下面的控
  件也跟着换。

## 选片不移动你的文件

设置 → 输出 → 分类目录布局，新增第三个选择：**平铺**。

照片照常检测、评星、写标签——星级、关键字、精选旗标、索尼 RAW 的侧车文件都
照写——但文件一个都不动。因为路径没变，你的 Lightroom 目录继续正常工作。

按星级、鸟种、对焦、连拍浏览筛选和以前完全一样。SuperPicky 读的是它自己的数
据库，不依赖你的目录结构。

连拍另有一个独立开关：你可以保留连拍检测（浏览器里分组、连拍组内 3 星限量），
同时不让连拍照片被塞进 `burst_001` 这样的子目录。

代表整个连拍组的那张，现在挑得更准了——会综合考虑对焦和眼睛清晰度，不再只看
鸟头锐度。

## 颜色标签变了，请留意

默认的颜色标签改成符合直觉的方向，绿色代表好，红色代表差：

| 照片 | 旧标签 | 新标签 |
|---|---|---|
| 飞鸟 | 绿色 | **蓝色** |
| 精准合焦 | 红色 | **绿色** |
| 脱焦、失焦 | （无） | **红色** |
| 普通合焦，或没有鸟 | （无） | （无） |

一张照片只有一种标签，飞鸟优先于其他。

**如果你在 Lightroom 建过「绿色代表飞鸟」的智能收藏夹，请改成蓝色。**

## 鸟名会写进你的关键字

识鸟结果置信度高时，鸟种名除了写进标题，现在也会写进照片的标准关键字。你可以
在 Lightroom 的关键字面板里直接按鸟种筛选。

你自己打的关键字绝对不会被改动，同一个文件夹重跑也不会产生重复。开关：设置 →
识鸟 →「识别后写入照片关键字」。

## 识鸟：地理数据整个重建了

SuperPicky 会根据拍摄地点缩小鸟种范围。这部分数据已经整个替换，因为旧数据有两
个实际的问题：

- **归化的鸟看不见。** 旧数据只记录一个鸟种最初来自哪里。在悉尼，这意味着家麻
  雀、原鸽、紫翅椋鸟、乌鸫永远识别不出来——照片再清楚也没用。
- **391 个鸟种在全球任何地方都识别不出来**，因为数据里根本没有它们。

这两个问题都没有了。新数据基于全球鸟类观测记录构建，覆盖 233 个国家。

同一块地方还修了两处：

- **鸟种稀少的地区不再让筛选失效。** 在记录鸟种很少的地方，旧的筛选会直接放弃、
  完全不筛——冰岛的照片因此被识别成小蓝企鹅和蓝脚鲣鸟。现在它改为逐步放宽搜索
  范围。433 张冰岛和法罗群岛的实测照片里，四个跨半球的错误全部消失，识别率反而
  **提高**了。
- **所有国家现在都能选。** 国家列表由筛选实际使用的同一份数据生成。此前列表里
  有 11 个国家背后根本没有数据（选了完全没作用），另有 14 个有数据的国家却不在
  列表里。冰岛和法罗群岛就在现在可选的国家之中。

请注意，手动选择的国家只对没有 GPS 的照片生效。

另外修复：用命令行以相对目录运行时，GPS 会被静默忽略，连同所有地理筛选一起失
效，而且没有任何错误提示。

## 速度快了约 30%

真实批次实测：495 张索尼 RAW，135 秒降到 95 秒。

改了什么：RAW 的元数据改为写进侧车文件，不再重写 RAW 本身；修掉了每张照片都
重写一遍缓存预览的 bug；元数据工具从一个进程改成两个，写入不再堵住读取。最后
这一项消除了每约 30 张照片就卡顿 3 秒的问题。

全屏浏览也更快、更省内存。长按方向键翻图现在完全走缓存，大图库下内存占用比以
前少约 2.4 GB。

## 更多鸟能被找到

第一遍完全找不到鸟时，SuperPicky 会用更高的细节再看一遍，并用识鸟模型确认结果。
这能救回那些因为太小、太远、或被当成飞机风筝而漏掉的鸟，同时挡住误检。开关：
设置 → 精选。

另外，当相机的对焦点判定这张脱焦、但实测鸟头锐度明显没问题时，以实测为准。真
正清晰的照片被压下去的情况变少了。

## 识鸟与 Lightroom 插件

- **鸟种颜值指数。** 每个鸟种现在都有一个颜值分，在详情面板显示，也可以用来筛
  选和排序。它描述的是**鸟种**，不是你的照片，对星级没有任何影响。
- **改正认错的鸟种。** 在照片上右键选「编辑鸟种」即可改正，也可以给还没有鸟名
  的照片指定鸟种。
- 关闭主窗口现在会让 SuperPicky 留在托盘继续运行，修复了 Lightroom 插件连不上
  的问题。
- 插件的「写鸟名」和「写描述」此前一直静默失效，现在都正常了，中文也正常——以
  前中文可能变成乱码。
- 识鸟服务几秒内就绪，插件不用再等十几秒。
- 恰好拍在赤道或本初子午线上的照片，不再被当成没有 GPS。
- Windows 上读取照片元数据时不再闪黑框。

## 所有设置集中到一处

原先分散的设置窗口全部合并成一个设置窗口，左边是分类：精选、识鸟、输出、外部
应用、关于。首页的快速面板与它双向同步。

这一版新增的设置：

- 鸟名的显示方式。
- 删除照片前是否需要确认。
- 清理全部预览缓存——只清掉当前文件夹缓存的预览和裁剪图，绝不碰你的原图。

技能档位和阈值是联动的：手动改了阈值就自动切到「自定义」。打开设置的快捷键是
逗号键：Mac 上按 Cmd 加逗号，Windows 上按 Ctrl 加逗号。

另外修复：Windows 深色模式下单选按钮看不见选中状态；浅色模式下设置窗口有一块
露灰。

## 结果浏览器

- 在浏览器里改鸟种或改星级，文件会自动移动到对应的文件夹。
- 数字键 0 到 3 直接设星级，上下方向键把星级加一减一。网格和全屏模式都能用。
- 「精选」改成持久保存的标记，缩略图上有皇冠角标。
- 缩略图标签精简为一行——有鸟名就显示鸟名，还没有鸟名就显示文件名，鼠标悬停可
  以看文件名。
- 详情面板新增鸟种一行，点一下可以复制鸟名。
- 窗口左右两边的对焦用语统一了：精焦、合焦、失焦。
- 全新的图标，全屏模式左侧增加了 Photoshop 风格的工具栏。

## 重置

重置现在会忽略系统隐藏文件，也不会再把照片漏在连拍文件夹里。它最后会做一遍摊
平，作为兜底。

## 界面更简单

为了让 SuperPicky 专注在选片上，几个非核心功能的菜单入口被移除了：应用内更新检
查、智能修图、裁剪建议、视频分析、纠错提交。

如果你在 4.3.0 用过「视频选鸟」，它的菜单在 4.5.0 里是有意去掉的，不是 bug。相
关代码还在，以后可能会回来。

## Windows：Lite 精简版没有了

Lite 的安装包很小，AI 引擎和模型留到第一次启动再下载。但实际上这让**总下载量**
变大了而不是变小：分两段大约 1.2 GB，而完整版一次到底只要 792 MB。更糟的是第二
段依赖别的服务器能连上，而这正是国内用户反复失败的地方。

Windows 现在有两个版本：CPU 版和 CUDA 版，都是完整的，装完就能离线使用。

**如果你现在用的是 Lite，请先卸载再安装完整版。** 两者用同一个安装目录，但在系
统看来是不同的程序，直接覆盖会让「添加或删除程序」里出现两个 SuperPicky。卸载
之后，你还可以把 Lite 下载的那套 AI 引擎删掉——完整版自带一套，永远不会去读它，
能腾出好几 GB。

安装体积也小了约 68 MB，因为新的鸟种地理数据取代了一个大得多的文件。

---

## 我该下载哪个

- **Apple Silicon 的 Mac**（M1 及更新）：标准安装包。
- **Intel 的 Mac**（大致是 2020 年以前）：Intel 安装包，走 CPU 运行。它是单独构
  建的，出现在下载页的时间可能会稍晚一点。
- **没有 NVIDIA 显卡的 Windows**：CPU 版，在 Release 页下载。
- **有 NVIDIA 显卡的 Windows**：CUDA 版。它太大放不进 GitHub，请走网盘链接。

4.5.0 没有 Lite 安装包。
