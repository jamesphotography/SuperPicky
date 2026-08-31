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

# SuperPicky 4.5.0

Stars are now given out by comparing photos within the same batch, you can cull
without moving your files, bird identification knows where birds actually live,
and everything runs about 30% faster.

## What's new

1. **Stars compare photos against each other, not against fixed scores.** One bar
   splits your batch into 3-star / 2-star / 1-star. Beginner keeps 40% as 3-star,
   Intermediate 30%, Master 20%.
2. **Every species keeps its own best shots.** With Bird ID on, the proportion is
   applied per species, so a hundred shots of one common bird can't crowd out
   everything else. Rare birds always keep at least one.
3. **New Flat mode: rate without moving files.** Everything gets tagged as usual,
   but nothing is moved, so your Lightroom folders keep working.
4. **Bird names go into your keywords.** Filter by species in Lightroom's keyword
   panel. Your own keywords are never touched.
5. **Common city birds can finally be identified.** House sparrows, feral pigeons,
   starlings and blackbirds used to be impossible in places like Sydney. So were
   391 species, anywhere on earth. The location data was rebuilt to fix it.
6. **No more penguins in Iceland.** In regions with few recorded species the filter
   used to give up entirely. Now it widens its search step by step. All 233
   countries can be selected, too.
7. **About 30% faster.** 495 Sony RAW files went from 135 seconds to 95.
8. **Full-screen browsing is much lighter.** Holding an arrow key runs off cached
   previews, and large libraries use around 2.4 GB less memory.
9. **A second look when no bird is found.** Birds that were too small, too distant,
   or mistaken for an aeroplane now get recovered.
10. **Beauty scoring looks at the bird, not the background.** A plain background no
    longer drags a good photo down.
11. **Distant birds are no longer over-rated for sharpness.** The old measurement
    quietly favoured small birds.
12. **All settings in one window**, with the home panel staying in sync. New:
    bird-name format, delete confirmation, and a button to clear preview caches.
13. **Faster culling in the browser.** Press 0-3 to set stars; changing a star
    rating or species moves the files for you; picks are marked with a crown.
14. **The Lightroom plugin works properly again.** Writing bird names and captions
    had been silently failing, Chinese text came back garbled, and the plugin often
    couldn't connect at all.

## Before you upgrade

- **Colour labels changed.** Flight is now **blue** (was green), critical focus is
  **green** (was red), and soft photos are **red**. If you built Lightroom smart
  collections on "green means flying", change them to blue.
- **Windows Lite is discontinued.** If you use Lite, uninstall it before installing
  the full version — otherwise you end up with two SuperPicky entries. You can then
  delete the AI engine Lite downloaded and free several GB.
- **Sharpness numbers for small-in-frame birds are lower than before.** That is the
  fix in point 11. Your thresholds still mean the same thing.
- **Video Bird Analysis is gone from the menus**, along with smart enhance, crop
  suggestions and update checks. This is deliberate, not a bug.

## Which download do I want

- **Mac, Apple Silicon** (M1 and newer): the standard installer.
- **Mac, Intel** (roughly pre-2020): the Intel installer, uploaded separately so it
  may appear a little later.
- **Windows without an NVIDIA card**: the CPU version, on this page.
- **Windows with an NVIDIA card**: the CUDA version, on the file-sharing links —
  it's too large for GitHub.

Full details for everything above: [ChangeLog-4.5.0-details.md](https://github.com/jamesphotography/SuperPicky/blob/master/docs/ChangeLog-4.5.0-details.md)

---

# SuperPicky 4.5.0（中文）

星级改成在同一批照片里互相比较，新增了不移动文件的选片模式，识鸟现在知道哪种鸟
实际住在哪里，整体速度快了约 30%。

## 这一版有什么新东西

1. **星级是照片之间互相比，不再是跟固定分数线比。** 一根配额条把整批照片分成
   3 星 / 2 星 / 1 星。新手档 3 星占 40%，进阶档 30%，大师档 20%。
2. **每个鸟种都保住自己最好的照片。** 开了识鸟后，比例按鸟种分别执行，一百张同
   一只常见鸟不会挤掉别的鸟种，罕见鸟至少留一张。
3. **新增平铺模式：只评星，不移动文件。** 标签照常写，但文件一个都不动，你的
   Lightroom 目录继续正常工作。
4. **鸟名会写进关键字。** 可以在 Lightroom 的关键字面板按鸟种筛选，你自己打的关
   键字不会被动。
5. **城市里的常见鸟终于能认出来了。** 家麻雀、原鸽、紫翅椋鸟、乌鸫这类鸟在悉尼
   这样的地方以前永远认不出，另有 391 个鸟种在全球任何地方都认不出。地理数据已
   整个重建修好了这个问题。
6. **冰岛不会再认出企鹅了。** 在记录鸟种很少的地区，旧的筛选会直接放弃不筛。现
   在改为逐步放宽搜索范围。233 个国家现在也全都能选。
7. **速度快了约 30%。** 495 张索尼 RAW 从 135 秒降到 95 秒。
8. **全屏浏览轻快多了。** 长按方向键翻图完全走缓存，大图库内存占用少约 2.4 GB。
9. **找不到鸟时会再看一遍。** 因为太小、太远、或被当成飞机而漏掉的鸟能被救回来。
10. **美学评分只看鸟，不看背景。** 背景平淡不会再拖累一张好照片。
11. **远处的小鸟不再被高估锐度。** 旧的测量方式暗中偏袒小鸟。
12. **所有设置集中到一个窗口**，首页面板与它同步。新增：鸟名显示方式、删除前确
    认、一键清理预览缓存。
13. **浏览器里选片更快。** 数字键 0 到 3 直接打星；改星级或改鸟种会自动帮你移动
    文件；精选的照片带皇冠角标。
14. **Lightroom 插件恢复正常。** 写鸟名和写描述此前一直静默失效，中文会变乱码，
    插件还经常连不上。

## 升级前请留意

- **颜色标签变了。** 飞鸟现在是**蓝色**（原来绿色），精准合焦是**绿色**（原来红
  色），失焦的照片是**红色**。如果你在 Lightroom 建过「绿色代表飞鸟」的智能收藏
  夹，请改成蓝色。
- **Windows Lite 精简版停产了。** 如果你用的是 Lite，请先卸载再装完整版，否则系
  统里会出现两个 SuperPicky。卸载后还可以把 Lite 下载的 AI 引擎删掉，能腾出好几
  GB。
- **画面里占比小的鸟，锐度数值会比以前低。** 这就是第 11 条修的问题，你的阈值含
  义没有变。
- **「视频选鸟」的菜单入口没有了**，一起去掉的还有智能修图、裁剪建议、更新检查。
  这是有意为之，不是 bug。

## 我该下载哪个

- **Apple Silicon 的 Mac**（M1 及更新）：标准安装包。
- **Intel 的 Mac**（大致 2020 年以前）：Intel 安装包，单独上传，出现时间可能稍晚。
- **没有 NVIDIA 显卡的 Windows**：CPU 版，本页可下。
- **有 NVIDIA 显卡的 Windows**：CUDA 版，走网盘链接，它太大放不进 GitHub。

以上每一条的详细说明：[ChangeLog-4.5.0-details.md](https://github.com/jamesphotography/SuperPicky/blob/master/docs/ChangeLog-4.5.0-details.md)
