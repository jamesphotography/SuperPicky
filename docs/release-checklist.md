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

### 4.1 更新 `docs/downloads_github.json`

**这个文件是手工维护的，且现在有两个下游依赖：**

1. 官网下载页（`superpicky.app`，GitHub Pages 从 `docs/` 发布）；
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
    "drive":  { "……": "Google Drive 链接" },
    "baidu":  { "……": "百度网盘链接（含 ?pwd=）" }
  }
}
```

`win_cuda` 只在 `drive` / `baidu` 里有（因为它进不了 Release，见第 3 节）。

> RC 版本是否要更新这个文件由你决定：更新了，所有正式版用户都会被提示升级到
> RC。通常 **RC 不更新**，只在正式版发布时更新。

### 4.2 其余分发动作

- GitHub Release 页面检查 prerelease 标记是否正确。
- 官网 `docs/` 下的下载页 HTML 若写死了版本号，一并更新。
- 网盘链接更新后同步到公众号 / 用户群。

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
