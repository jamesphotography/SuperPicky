# 发布操作清单 / Release Checklist

打包与发布 SuperPicky 时按本清单逐项执行。每一条都对应过往踩过的坑，跳过任何
一条都会产生用户可见的问题。

Follow this checklist when packaging and releasing SuperPicky. Every item maps to
a problem that has actually happened; skipping any of them produces a
user-visible defect.

---

## 1. 发版前 / Before tagging

### 1.1 版本号

改 `constants.py` 的 `APP_VERSION`：

| 阶段 | 写法 | 示例 |
| --- | --- | --- |
| RC | `主.次.修RC序号`（大写 RC，**无连字符**） | `4.6.0RC1` |
| 正式版 | `主.次.修` | `4.6.0` |

这是版本号的唯一定义处，`.spec` 从这里导入（`SuperPicky.spec:6`），并写入
`CFBundleVersion` / `CFBundleShortVersionString`。CI 可用环境变量
`SUPERPICKY_APP_VERSION` 覆盖，但正常发布不要依赖它。

### 1.2 ChangeLog.md —— 必改

**CI 直接把 `ChangeLog.md` 全文当作 GitHub Release 的正文**
（`build-release.yml` 的 `body_path: ChangeLog.md`）。不更新它，新版本的发布页
会原样显示上一版的说明，对下载者是误导。

格式沿用既有惯例：

```markdown
# SuperPicky 4.6.0 RC1

**What's new since 4.5.0:**

- **一句话标题。** 具体说明……
```

写给普通用户看，不用 emoji，不写内部实现（CI 修复、重构之类不要进）。
上一版的正文保留在下方，用 `---` 分隔。

### 1.3 三分支状态

`dev` / `nightly` / `master` 三条线。历史惯例是 **tag 打在 `master`**
（RC6 起如此），发版前先把 `dev` 合进 `nightly` 和 `master`。

合并时 `constants.py` 的版本号**必定冲突**，取 `--theirs`（即被合入方的新版本号）。

> 例外：如果明确只想从 `dev` 出一个 RC 包，可以直接在 `dev` 上打 tag —— CI 只认
> tag 不认分支。但要清楚此时 tag 只存在于 dev 线，事后仍需把分支收敛回来，
> 别让三条线长期分叉。

---

## 2. 打 tag 触发构建 / Tagging

```bash
git tag v4.6.0-rc1        # 小写、带连字符，与历史一致
git push origin v4.6.0-rc1
```

- 触发条件是 tag 匹配 `v*.*.*`（`build-release.yml` 的 `on.push.tags`），
  **push 分支不会触发构建**。
- tag 含 `-rc` 或 `-RC` 时，CI 自动标记为 prerelease，不会被当成 latest 推给
  用户（`build-release.yml` 的 `prerelease:` 表达式，大小写都认）。
- 正式版 tag **不要**加 `-final` 之类后缀 —— prerelease 判断只看 `-rc`，多余的
  后缀会让它被当成正式版处理，但版本名会变得难看。

也可在 Actions 页手动 `workflow_dispatch`，需填 version，并可选择只构建某个平台。

---

## 3. 构建产物 / Artifacts

- **Windows CUDA 包 >2GB**，超过 GitHub Release 单 asset 上限（2 GiB），
  **只会留在 Actions artifact 里，不会自动进 Release**。必须手工下载后上传到
  网盘（Google Drive / 百度网盘）。
- 下载 2GB 级 artifact 用 `curl -C -` 续传；解压报 "EOCD 缺失" 基本都是没下完。
- **Intel Mac 版需要手工构建上传**，CI 不产出。

---

## 4. 发版后 —— 最容易漏的一步 / After release

### 4.1 更新 `downloads_github.json`（在**站点仓库**里）

> ⚠️ **文件位置早就变了。** 本节此前写的是「`docs/downloads_github.json`，
> GitHub Pages 从 `docs/` 发布」——那是 Cloudflare 迁移之前的事。线上那份现在是
> **`SuperPicky-Site` 仓库的 `site/downloads_github.json`**，由 Workers 提供。
>
> 本仓库的 `docs/downloads_github.json` 仍在磁盘上，但**已经不再被任何线上地址
> 提供**。它最危险的地方恰恰是它还在：改它、提交它，一切看起来都做完了，
> 而线上一个字节都没变。（清理 `docs/` 是另一件独立待办。）

**这个文件是手工维护的，有两个下游依赖：**

1. 官网首页与下载页（`superpicky.app`，由 `SuperPicky-Site` 的 Worker 提供）；
2. **应用内「关于」页的「检查更新」功能**（4.6.0 新增，见
   `tools/site_version.py`）—— 它读的就是这个 JSON 的 `latest.tag`。

**不更新 = 所有用户点「检查更新」永远看到旧版本号**，这个功能等于没做。

需要更新的字段：

```json
{
  "latest": {
    "tag": "v4.6.0",
    "updated_at": "……",
    "files":  { "mac_arm64": "……dmg", "mac_intel": "……dmg", "win_cpu": "……exe" },
    "drive":  { "mac_arm64": "/dl/mac_arm64-v4.6.0-gdrive", "……": "……" },
    "baidu":  { "mac_arm64": "/dl/mac_arm64-v4.6.0-baidu",  "……": "……" }
  }
}
```

`win_cuda` 只在 `drive` / `baidu` 里有（因为它进不了 Release，见第 3 节）。

> ⚠️ **提交时，`drive` / `baidu` 里必须是 `/dl/<id>` 站内路径，不是网盘直链。**
> 真实地址存在 `SuperPicky-Site/src/dl-map.json` 里，由 Worker 做 302 跳转。
> 留着网盘 URL 就绕过了下载统计（四个渠道正是靠 `/dl/` 才有统一口径），
> 并会让 `tests/dl-map.test.mjs` 变红。
>
> 两种情形分开记：
> - **给已有版本换网盘链接** → 只改 `src/dl-map.json` 里那一条的 `url`，
>   `downloads_github.json` 一个字不动（`/dl/<id>` 不变，已发出去的链接继续有效）。
> - **发新版本** → 见下面的四步：第 1 步先把新直链填进 `drive` / `baidu`，
>   第 2 步的脚本会把它们收进映射表并改写成 `/dl/<id>`。

#### 按这个顺序做，一步都不能跳

全部在 `SuperPicky-Site` 仓库里：

```bash
# 1. 手工改 site/downloads_github.json：
#      latest.tag / latest.updated_at / latest.files  → 新版本
#      latest.drive / latest.baidu                    → 这一轮的**真实网盘直链**
#    （对，这一步先填直链。下一步的脚本会把它们收进 src/dl-map.json 并
#      把这两块改写回 /dl/<id>。**不要就这么提交**——留着直链就是绕过统计。）

# 2. 重新生成下载映射表：先看 dry-run 报告，确认新版本的 8 条 id 都对，再写盘
node scripts/build-dl-map.mjs
node scripts/build-dl-map.mjs --apply

# 3. 跑测试（此时 tests/dl-map.test.mjs 的版本号绊线会红，这是对的）
npx vitest run

# 4. 把 tests/dl-map.test.mjs 里写死的 tag 改成新版本，再跑一次，应全绿
npx vitest run
```

**第 2 步是本节最容易漏、后果最安静的一步。** 首页的下载按钮是 JS 用
`latest.tag` **现拼**出 `/dl/<platform>-<tag>-<channel>` 的，而映射表
（`src/dl-map.json`）是**提前静态生成**的。只改 tag 不重跑脚本，首页就会拼出
映射表里不存在的 id ——**四个平台的 GitHub 与大陆镜像下载按钮全部 404**，
而且线上没有任何东西会报警：JSON 合法、映射表自洽、页面照常渲染。

关于第 1 步为什么要先填直链：脚本对 `drive` / `baidu` 的做法是「读当前值 →
按新 tag 生成 id → 把真实地址登记进映射表 → 把这两块改写成 `/dl/<id>`」。
若你只改了 tag 而把这两块留成上一版的 `/dl/...`，脚本会拿这个站内路径当
「真实地址」，出表自检时判为非法 url 并**拒绝写盘**（已实测：8 条
`非法 url：mac_arm64-v4.6.0-gdrive → /dl/mac_arm64-v4.5.0-gdrive`，退出码非零）。
这是好事——它拦得住，只是你得知道该怎么继续。

第 4 步那个写死的版本号是**故意**的发版绊线（`dl-map.test.mjs` 里有整段说明）。
它红了不是「测试过期」，是在提醒你第 2 步没做。**顺序不能反**——改它是确认
前面都做完了的签字，不是让红变绿的手段。

归档页 `site/downloads.html` 加不加新版本一行，是独立的一步：加了行、行里贴
直链，第 2 步会一并把它们收进映射表并改写成 `/dl/<id>`；不加也不影响首页。

> 两个仓库都**没有测试 CI**（站点仓库连 `.github/` 都没有）。上面这几条
> `npx vitest run` 是这条链路上唯一会跑的地方，跳过就等于没有任何守卫。

> RC 版本是否要更新这个文件由你决定：更新了，所有正式版用户都会被提示升级到
> RC。通常 **RC 不更新**，只在正式版发布时更新。

### 4.2 其余分发动作

- GitHub Release 页面检查 prerelease 标记是否正确。
- 站点仓库 `site/` 下的页面 HTML 若写死了版本号，一并更新
  （`grep -rn '4\.[0-9]\.[0-9]' site/*.html` 自查），然后 `npm run deploy`。
- 网盘链接更新后同步到公众号 / 用户群。
- **查一次 `/stats` 看板**：`https://superpicky.app/stats?token=<STATS_TOKEN>`。
  发版之后必然有人启动新版本，此时 `dailyActive` 仍为 0 就意味着**遥测管道
  断了，不是没人用**——这是唯一能把这两件事区分开的时刻。判读方法见
  `app_user_stat/README_TELEMETRY.md`「如何发现它又死了」。
  （4.x 的 Countly 端点域名失效后静默投递了数月无人察觉，就是因为没有这一步。）

---

## 5. 已知约束（不要试图"修复"）/ Known constraints

- **应用内不做自动更新检测**。`tools/update_checker.py` 的
  `ONLINE_UPDATE_CHECK_DISABLED = True` 自 4.3.0 起未变。动因：CUDA 包进不了
  Release、Full 与 Lite 的 inno AppId 不同会脏覆盖同一目录、补丁覆盖层会用旧代码
  覆盖新代码。这三条至今成立。
- 「关于」页的**按需**版本查询不受该开关约束，因为它只在用户点击时读一个静态
  JSON，不碰 Release API、不下载、不安装。
- 官网域名是 **`superpicky.app`**（见 `docs/CNAME`）。
  `superpicky.jamesphotography.com.au` 已无 DNS 记录，是死链，任何地方都不要再写。
