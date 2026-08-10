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
