# 使用统计与访问统计设计 / Usage & Visit Analytics Design

- 日期 / Date: 2026-08-21
- 状态 / Status: 设计已确认，待实现 / Design approved, pending implementation
- 涉及仓库 / Repos: `SuperPicky2026`（app 端）、`SuperPicky-Site`（Worker + 官网）

---

## 1. 背景：现状盘点 / Background

动机是「不知道多少人用、多少人下载访问」。核查后发现这不是一件事，而是三件卡在不同地方的事。

### 1.1 App 端遥测早已存在，但服务端已死

`app_user_stat/telemetry.py`（685 行，PR #78，2026-03 由 OscarKing888 贡献）是一套完整的 Countly
客户端：匿名 device_id、中英文同意弹窗、`install` / `app_start` / `heartbeat_weekly` 三个事件、
7 天心跳节流、后台线程投递、异常全吞。CI 的 `build-release.yml` 在 Windows（L46-48）与 macOS
（L197-199）两个 job 都注入了 `COUNTLY_APP_KEY` / `COUNTLY_SERVER_URL`，仓库 secrets 确实已配
（2026-03-23）。

**但端点域名已不存在：**

```
endpoint = https://superpicky-4825f5a76c1f2.flex.countly.com/i
dig      = 空
curl     = Could not resolve host
```

该域名是 countly.com 的子域，不可控、无法找回。由于 telemetry 代码按设计吞掉所有异常（正确的
设计——统计不能拖垮 app），后果是**数月来所有打包版都在向一个不存在的域名投递，静默失败，
零数据落地，且无人知晓**。

推测原因：Countly Flex 免费额度仅 500 MAU，超出后 Tier 1 为 $80/月；实例大概率因试用到期或
超额被回收。

### 1.2 官网零访问统计

`SuperPicky-Site` 中检索不到任何 analytics 代码。`wrangler.jsonc` 的 `observability: enabled`
是 Worker 运行日志，不是访问分析。站点当前为**纯静态 assets Worker，无 `main` 入口、无任何
Worker 脚本代码**。

### 1.3 下载量一直在记，只是无人查看

GitHub Releases API 提供现成的 `download_count`，累计 **12,706 次**（2026-08-21 查询）。
其余两个渠道无数据来源：

| 渠道 | 能否取得下载数 | 原因 |
|---|---|---|
| GitHub Releases | 可以 | API 直接提供 `download_count`，历史完整 |
| Google Drive | 不能 | 下载事件仅存在于 Google Workspace 企业版审计日志；个人账号无此报表，分享链接对拥有者不显示计数 |
| 百度网盘 | 不能 | 分享链接无统计，无开放 API |

官网 `downloads.html` 同时挂着这三类链接，因此仅用 GitHub 数据会**系统性低估国内用户**，
且低估幅度未知。

---

## 2. 目标与非目标 / Goals & Non-Goals

### 目标

1. **活跃用户**：日活/周活去重人数、版本分布、Win/Mac 占比。
2. **官网访客**：每日访客、来源、热门页面。
3. **下载量**：GitHub / Google Drive / 百度网盘三渠道统一口径。

### 非目标（明确排除）

- **功能级埋点**（哪个功能被用、在哪一步流失）。数据最有价值但最敏感，本轮不做。
- **留存曲线**。按日轮换 ID 方案下无法计算，已知并接受（见 §4.2）。
- **个体用户追踪**。任何情况下都不做。

### 已确认的决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 数据后端 | 自建 Cloudflare Worker + Analytics Engine | $0、无 MAU 天花板、数据自有、不会再消失一次 |
| 采集同意方式 | 默认开启 + 设置中心可关（opt-out） | 由项目所有者拍板 |
| 匿名 ID | 按日轮换哈希 | 满足全部目标，且不构成持久标识符 |
| 下载统计 | 出站跳转计数 | 三渠道统一口径，兼得链接可替换性 |
| Worker 部署 | 复用官网 Worker，不新建 | 避免第二套部署/域名/secret |

---

## 3. 架构 / Architecture

```
┌─ 官网访客 ─────────────────────────────────┐
│  访客 → superpicky.app                     │
│         superpicky-site Worker             │
│         ├─ env.ASSETS.fetch()  返回静态页    │
│         └─ writeDataPoint("pageview")      │
└────────────────────────────────────────────┘
                      ↓
┌─ 下载跳转 ─────────────────────────────────┐
│  点击 → superpicky.app/dl/<id>             │
│         ├─ writeDataPoint("dl")            │
│         └─ 302 → GitHub / GDrive / 百度网盘  │
└────────────────────────────────────────────┘
                      ↓
          ┌───────────────────────────┐
          │  Analytics Engine         │
          │  数据集 superpicky_stats   │
          │  免费 10万写/天，保留 3 个月 │
          └───────────────────────────┘
                      ↑
┌─ App 活跃 ─────────────────────────────────┐
│  SuperPicky 启动 → POST /t                 │
│         └─ 校验 + writeDataPoint("app")     │
└────────────────────────────────────────────┘
                      ↓
        GET /stats?token=… → 自制看板
        （AE SQL 查询 + 实时拉 GitHub API）
```

**为何官网走服务端记录而非 JS beacon**：Worker 本就处理每个请求，顺手记账即可——无法被广告
拦截插件屏蔽、无 cookie、无需改动任何 HTML、不受第三方脚本在国内网络下可达性的影响。代价是
无法区分真人与爬虫，需在 Worker 内按 User-Agent 打标。

**为何复用官网 Worker**：官网 Worker 已部署、域名已接管、`observability` 已开。新建第二个
Worker 意味着第二套部署流程、第二个域名、第二份 secret，无收益。

**为何下载量不落库**：GitHub 的历史下载数由 API 实时提供，无需自建存储。仅「出站跳转」这一
新增维度写入 Analytics Engine。

### 3.1 部署形态变更的风险与缓解

站点将从 **assets-only** 变为 **Worker script + assets binding**。这意味着它从「不可能挂」
变成「脚本出错即整站不可用」。三道保险：

1. 静态资源**先取到再记账**——`response` 在记账之前就已得到。
2. 记账走 `ctx.waitUntil()`，不阻塞响应返回。
3. `safeLog()` 内部 try/catch 全吞，任何失败都不触碰 `response`。

最坏情况是丢失统计，而非丢失站点。

**必须实测验证**：加 `main` 后 `not_found_handling: "404-page"` 是否仍生效。
`env.ASSETS.fetch()` 理论上继承 assets 配置，但该行为是此前专门为 SEO 踩坑后确定的
（SPA 模式会让死链以 200 被当作首页重复内容收录），必须确认 404 页仍返回 404 状态码。

---

## 4. 数据模型 / Data Model

单一 Analytics Engine 数据集，三类事件以 `index1` 区分。

| 事件 | index1 | blobs | doubles |
|---|---|---|---|
| 页面访问 | `pageview` | 路径、来源域、国家、设备类型、是否爬虫 | 1 |
| App 心跳 | `app` | 版本、OS、架构、语言、事件类型、日轮换ID | 1 |
| 下载跳转 | `dl` | 渠道、版本、平台、来源页、是否爬虫 | 1 |

限制余量：单次调用上限 20 blobs / 20 doubles / 1 index，blobs 合计不超过 16KB，均远未触及。
免费额度 10 万数据点/天写入、1 万次查询/天，保留 3 个月。按当前体量（数千用户 × 每周 3 事件
+ 官网访问）用量不足额度 1%。

**保留期 3 个月的后果**：超过 3 个月的历史数据会丢失。若需长期趋势，需另行定期快照汇总值
（本轮不做，留待有数据后再评估）。

爬虫按 User-Agent 识别后**打标而非丢弃**，看板默认排除但可对照查看。

### 4.1 匿名 ID 方案

上报 `hash(当天日期 + 本地安装ID)`：

- 本地安装 ID 存于 `telemetry_state.json`，**永不上报**。
- 上报值每日变更，跨日不可关联。
- 可计算：日活/周活去重、版本分布、平台占比。
- 不可计算：留存（上周用过的人本周是否还在）、跨日 MAU 去重。

### 4.2 已知的数据偏差

诚实记录，避免日后误读数字：

1. **存量用户永久缺失**。已发布的包硬编码了失效域名，无法补救。只有新版本才会产生数据，
   因此上线初期的曲线是「新版本渗透率」而非「真实用户增长」。
2. **下载跳转仅覆盖官网点击**。公众号 / 微信群里直接发出的网盘链接不计入——除非那些渠道也
   改发 `superpicky.app/dl/<id>`（推荐，见 §5.3）。
3. **跳转计数是「下载意向」而非「下载完成」**。点击后未下完、或 Google Drive 仅打开预览页
   即关闭，都会计数。GitHub 的 `download_count` 本质相同（开始下载即 +1），故三渠道口径一致。
4. **opt-out 关闭率未知**。数字为「实际用户数 × (1 − 关闭率)」，绝对值仍有系统性低估。

---

## 5. 实现要点 / Implementation

### 5.1 Worker 端（`SuperPicky-Site`）

`wrangler.jsonc` 新增 `main` 入口与 Analytics Engine binding，保留现有 `assets`、`routes`、
`workers_dev`、`not_found_handling` 全部配置不变。

```js
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname.startsWith('/dl/')) return handleRedirect(url, request, env, ctx);
    if (url.pathname === '/t')           return handleTelemetry(request, env, ctx);
    if (url.pathname === '/stats')       return handleStats(request, env);

    const response = await env.ASSETS.fetch(request);
    ctx.waitUntil(safeLog(request, env));
    return response;
  }
};
```

`/dl/<id>` 的映射表（id → 真实 URL）与渠道/版本/平台元数据放在 Worker 内的单一常量表中，
换网盘链接只改这一处。

**`/t` 的「校验」指什么——明确其边界**：客户端是开源的，任何密钥都能从源码或安装包中取出，
因此 `/t` **不做身份认证**，它是一个公开端点，任何人都可以伪造数据点。这是开源桌面客户端的
固有属性，`README_TELEMETRY.md` 中原作者已就 Countly app key 说明过同一件事。校验仅指：

- payload 结构与字段白名单校验（拒绝畸形/超长内容，防止污染数据集）
- 基于 Cloudflare 边缘的速率限制（防止单点刷量把免费额度打满）

因此这套数字的定位是**趋势观察，而非可审计的精确指标**。若日后需要防刷保证，需引入服务端
签名或代理，属另一个议题。

### 5.2 App 端（`SuperPicky2026`）

`telemetry.py` 的骨架全部保留：后台线程投递、异常全吞、状态持久化、心跳节流。只换三处：

1. **发送层**（约 60 行）：Countly `/i` 表单格式 → `POST https://superpicky.app/t` 发送 JSON。
   老版本硬编码失效域名、无法挽救，因此无向后兼容负担，一次改干净。
2. **开关搬家**：删除 `telemetry_consent.json` 与配套的 `_show_consent_dialog` 流程，改用
   `advanced_config` 的 `telemetry_enabled`（默认 `True`）。
   **这是 CLAUDE.md 强制的 SSOT 规则——现有的独立 json 属于违规**，本次一并修正。
   新增字段需同时在 `DEFAULT_CONFIG` 加字段并补 `@property` / `set_*`。
3. **首启告知**：opt-out 不等于不告知。onboarding 加一句说明 + 隐私链接；设置中心「关于」页
   放开关。缺了这一步，opt-out 在合规与社区观感上都站不住。

`telemetry_state.json`（本地安装 ID、上次心跳时间）**保留为独立文件**——它是运行时状态而非
用户设置，不进 `advanced_config`。

**必须处理的接线时序**：`main.py:253` 当前是

```python
bootstrap_telemetry(_main_window, on_ready=_main_window.run_startup_prompts)
```

即**启动期弹窗（onboarding 等）被挂在遥测同意流程完成之后**（`main_window.py:4352` 的注释
说明了这是为避免 onboarding 被重复触发）。移除阻塞式同意弹窗后，这条链路的时序改变：
`on_ready` 必须仍然**恰好触发一次**，且不再有等待用户点击的阶段。实现时需确认——

- 遥测被关闭（`telemetry_enabled = False`）时 `on_ready` 依然触发，否则 onboarding 永不出现；
- 遥测初始化抛异常时 `on_ready` 依然触发（现有代码异常全吞，需确认回调不被一并吞掉）。

这是本次改动中最容易引入回归的一处：漏掉会导致新用户看不到 onboarding，且不会有任何报错。

上报字段沿用 `_build_common_fields()` 现有内容（`app_version` / `os` / `arch` /
`python_version` / `locale`），不新增任何字段。

### 5.3 官网链接改造

`downloads.html` 中数十条 GitHub / Google Drive / 百度网盘链接需批量替换为 `/dl/<id>`。
**必须写脚本处理，不可手工**（`SuperPicky-Site/scripts/` 下已有同类脚本可参照）。

附带收益：网盘链接自此可替换。日后换网盘、链接失效、新增渠道，只改 Worker 内的映射表，
已发布的旧链接依然有效，无需重发公告。建议公众号 / 微信群后续也改发 `/dl/<id>` 链接。

### 5.4 看板

`/stats?token=<secret>` 返回自制单页：Worker 用 secret 中的 API token 查询 Analytics Engine
的 SQL 接口，外加实时拉取一次 GitHub Releases API 汇总下载量。

本轮不做实时刷新、时间范围选择器等交互——先出数，界面待有数据后再迭代。

---

## 6. 测试与验证 / Testing

### Worker 端（`SuperPicky-Site` 已有 vitest）

1. `safeLog` 抛异常时，页面响应不受影响（注入失败的 AE binding 断言 response 正常）。
2. `/dl/<id>` 返回 302 且 Location 正确；未知 id 返回 404 而非 500。
3. `/t` 拒绝畸形 payload，且拒绝时不写入数据点。
4. `/stats` 无 token 或 token 错误时返回 401。
5. **实测**：部署后确认 404 页仍返回 404 状态码（§3.1）。
6. **实测**：`workers_dev` 地址仍可访问（切换域名前唯一不依赖 DNS 的验证入口）。

### App 端（`SuperPicky2026`）

1. `telemetry_enabled = False` 时不发起任何网络请求。
2. 日轮换 ID 在同一天内稳定、跨日变化，且本地安装 ID 不出现在任何上报 payload 中。
3. 端点不可达时启动不受阻塞、不报错（回归现有的「异常全吞」行为）。
4. 心跳节流仍为 7 天。
5. `advanced_config` 的 setter clamp 与设置中心 UI 控件行为一致（CLAUDE.md 要求）。
6. **测试必须隔离全局配置**——注入 `config_file`，不得写入本机真实 `advanced_config.json`。
7. 变更文件跑 `python -m py_compile`。

---

## 7. 未决与后续 / Open Items

- 超过 3 个月的历史数据保留策略（§4 保留期），待有数据后评估。
- 功能级埋点是否开启（本轮明确排除）。
- 公众号 / 微信群渠道是否统一改用 `/dl/<id>` 链接（推荐，但属运营决策）。
- CI 中 `COUNTLY_APP_KEY` / `COUNTLY_SERVER_URL` 两个 secret 及
  `scripts/prepare_telemetry_build.py` 的注入步骤，在新方案落地后应一并清理。
