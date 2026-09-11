# SuperPicky 4.6.3 RC3

**What's new since RC1:**

There are no changes to the app itself in RC2 — it is the same build as RC1.
What changed is how the NVIDIA GPU version reaches you.

1. **The CUDA (NVIDIA GPU) installer is now on the GitHub release page.** Until
   now it was the one download you could not get from GitHub: it was believed to
   exceed GitHub's 2 GiB per-file limit, so every release it had to be copied by
   hand to a cloud drive. It never actually exceeded the limit — the file is
   1.97 GiB, which many tools display as "2.11 GB", and that display is what the
   assumption was built on. It now ships in the release like every other
   download. The cloud-drive mirrors stay as they are.

2. **The CUDA installer is smaller.** A cuDNN component that only serves
   recurrent and attention networks was being bundled, and this app runs neither
   — every model in it is a plain convolutional network. Removing it takes
   roughly 80 MB off the download. Nothing about detection, identification or
   scoring changes.

The CPU version for Windows and the macOS version are byte-for-byte unaffected
by both changes.

---

# SuperPicky 4.6.3 RC3（中文）

**RC1 以来的变化：**

RC2 的程序本身与 RC1 完全相同，改的是 N 卡（CUDA）版怎么送到你手上。

1. **CUDA（N 卡）安装包现在直接放在 GitHub 发布页上。** 在此之前它是唯一一个
   在 GitHub 上下不到的版本：一直以为它超过了 GitHub 单文件 2 GiB 的上限，所以
   每次发版都要手工搬到网盘。其实它从来没超过——文件是 1.97 GiB，只是很多工具
   把它显示成「2.11 GB」，当初就是照着这个显示下的判断。现在它和其他下载一样
   随发布页一起发出。网盘镜像照旧保留。

2. **CUDA 安装包变小了。** 包里一直带着一个只服务循环网络与注意力网络的 cuDNN
   组件，而这个程序里一个都没用到——所有模型都是普通的卷积网络。去掉它，下载量
   少了大约 80 MB。识别、检测、评分的行为一点不变。

这两项改动都不影响 Windows CPU 版和 macOS 版，那两个包与 RC1 逐字节相同。

---

# SuperPicky 4.6.3 RC1

This release is about correcting the AI when it is plainly wrong, and about
windows that stay where you can reach them.

## What's new

1. **Tell SuperPicky "this is not a bird".** Every so often the detector calls a
   crocodile — or a branch, or a rock — a bird, and until now there was no way to
   say otherwise: you could change the species, but not remove it. The species
   dialog now has a **Not a bird** button. It clears the species, drops the photo
   to 0 stars, moves it to the reject pile and takes it out of the report's
   species list, all in one step. Tick several thumbnails first and the whole
   selection goes at once. Got it wrong? Just set a species again — the photo
   counts as a bird once more (you will want to restore its rating yourself,
   since naming a species deliberately leaves your stars alone).

2. **The species picker opens with the birds you actually photographed.** When a
   bird is misidentified it is usually mistaken for the species next to it, and
   that one was very likely photographed the same day. The dialog now lists this
   shoot's species first, most-shot at the top, before you type anything; once
   you do type, they still sort to the top of the matches.

3. **The results browser opens maximized.** On a 14-inch MacBook — or at any
   enlarged text size — the old fixed window squeezed the three-column layout.
   It now opens maximized, keeping the title bar and toolbar (this is not
   macOS full screen).

4. **Pick several folders at once when merging.** The system folder chooser now
   accepts a multiple selection, so building a merged set no longer means
   opening the dialog once per folder.

5. **Your own rarity index sits next to the global one.** The bird-name lookup
   now shows the custom rarity score beside the global figure, the same way the
   results browser does.

## Fixes

6. **The main window no longer opens with its buttons off-screen.** The window
   remembered a size and position without checking whether they still fit the
   screen, so a window saved while the Dock was hidden came back with **Start**
   and **Reset** cut off underneath a pinned Dock — visible in the layout,
   impossible to click. The saved placement is now clamped into the screen's
   usable area, keeping your preferred position while guaranteeing the whole
   window is reachable.

7. **78 common species are no longer labelled "legendary".** A flaw in how
   rarity data was matched stored a genus-level key for species whose exact
   match failed, and a genus key finds no occurrence records — so the count came
   back as zero and the bird was scored as impossibly rare. The Eastern Cattle
   Egret, one of the most widespread herons in the world, was rated legendary.
   All affected entries have been rebuilt.

8. **Changing a species now updates the thumbnail.** The caption under each
   thumbnail was written once when the grid was built and never again, so after
   correcting a species the grid kept showing the old name until you reopened
   the folder.

9. **You can finally see it when a file move fails.** Corrections that could not
   move a file were supposed to report the reason; the notice was scheduled in a
   way that never actually ran, so a failed correction looked exactly like a
   successful one. It now reaches you.

---

# SuperPicky 4.6.3 RC1（中文）

这一版是关于两件事：AI 认错得离谱时你能纠正它，以及窗口别跑到你够不着的地方。

## 这一版有什么新东西

1. **可以告诉 SuperPicky「这不是鸟」。** 识别偶尔会把鳄鱼——或者一根树枝、一块
   石头——当成鸟，而在此之前你拿它没办法：能改鸟种，却不能说「这压根不是鸟」。
   改鸟种的弹窗里现在多了**「这不是鸟」**。点一下，鸟种清空、降为 0 星、移进
   「其他鸟类/0星_放弃」，报告的鸟种名录里也不再有它，一步到位。先勾几张再点，
   整批一起标掉。标错了？重新指定鸟种即可，它又算作有鸟——星级要你自己升回来，
   因为改鸟种不擅自动你的星。

2. **选鸟种时先列出你今天真拍到的那些。** 认错多半是认成了隔壁那种，而那种当天
   通常也拍到了。弹窗打开时（还没打字）先按张数列出本次拍到的鸟种；开始搜索后，
   它们仍然排在匹配结果的最前面。

3. **选鸟结果浏览器默认最大化打开。** 在 14 寸 MacBook 上，或者你把系统字号调大
   之后，原先固定大小的窗口会把三栏布局挤变形。现在最大化打开，标题栏和工具栏
   都还在（不是 macOS 那种全屏）。

4. **合并目录时可以一次选中好几个。** 系统的目录选取框现在支持多选，攒一份合并
   清单不必再一个一个地开弹窗。

5. **查询鸟名时并排显示你自己的罕见指数。** 鸟名查询的详情区现在把自定义罕见
   指数摆在全球罕见度旁边，与选鸟结果里的显示方式一致。

## 修复

6. **主窗口不会再把按钮开到屏幕外面。** 窗口会记住上次的大小和位置，却从不检查
   这套数值在当前屏幕上还放不放得下——于是在 Dock 自动隐藏时存下的位置，等你把
   Dock 固定显示之后再打开，**「开始处理」**和**「重置」**就被 Dock 压在下面：
   布局里明明有，就是点不到。现在会把记住的位置夹进屏幕可用区域，既保留你惯用的
   位置，也保证整个窗口都够得着。

7. **78 个常见鸟种不再被标成「传奇」。** 罕见度数据在匹配失败时会退而存下属一级
   的标识，而属一级的标识查不到任何观测记录——数量返回 0，于是这个鸟种被算成了
   稀世罕见。牛背鹭，全世界分布最广的鹭之一，就这么成了「传奇」。所有受影响的
   条目都已重建。

8. **改完鸟种，缩略图上的名字会跟着变了。** 缩略图下方那行字只在建网格时写过
   一次，之后再没更新过，所以改完鸟种，网格里显示的还是旧鸟名，除非你重开目录。

9. **文件搬不动的时候，你终于能看见了。** 改鸟种时若文件移动失败，本该告诉你
   原因，但那条提示的触发方式实际上从来没执行过——失败看起来和成功一模一样。
   现在它会真的弹出来。

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
