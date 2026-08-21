# 使用统计与访问统计 实现计划 / Usage & Visit Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 SuperPicky 能回答「多少人在用、多少人访问官网、各渠道下载多少」，数据落在自有的 Cloudflare Analytics Engine。

**Architecture:** 复用官网现有 Cloudflare Worker（目前是纯静态 assets，本计划为其新增 `main` 入口），以三个路由承接三类事件并写入同一个 Analytics Engine 数据集；App 端把既有 Countly 投递改为向该 Worker 发送 JSON，匿名 ID 改为按日轮换。

**Tech Stack:** Cloudflare Workers（JS，ESM）、Workers Analytics Engine、wrangler 4.x、vitest 3.x；Python 3.12+/PySide6（app 端）。

**Spec:** `docs/specs/2026-08-21-usage-analytics-design.md`

## Global Constraints

- **两个仓库**：Phase A 全部在 `~/Documents/JamesAPPS/SuperPicky-Site`；Phase B 全部在 `~/Documents/JamesAPPS/SuperPicky2026`。**每个 Task 的 Files 段已标注仓库，提交也在对应仓库进行。**
- **统计代码永远不能影响页面返回**：静态资源先取到再记账、记账走 `ctx.waitUntil()`、记账函数内部 try/catch 全吞。
- **`run_worker_first` 命中的请求算 Worker 调用，免费额度 10 万/天，超出后返回 429 而不是回退到静态资源**（即整站不可用）。Task 1 的负规则把 css/js 与机器读取的文件排除在外，使调用数约等于页面浏览量，余量 10 万页面浏览/天。**任何扩大 `run_worker_first` 匹配范围的改动都会按比例吃掉这个余量。**
- **UTF-8**：所有文件读写显式 `encoding='utf-8'`；中文注释 + 同格式英文注释（见 `CLAUDE.md`）。
- **不得用 shell 脚本处理含中文的文件**，Python 或 Node 优先。
- **App 端设置项唯一存储是 `advanced_config`**，禁止新增独立 json 存放设置（`CLAUDE.md` 强制）。
- **App 端测试必须注入 `config_file`**，不得写入本机真实 `advanced_config.json`。
- **`downloads_github.json` 是已发行应用读取的线上契约**（`tools/site_version.py`）。**已核实：应用只读 `latest.tag`，不读 `drive`/`baidu`/`files`**，故改写后两者安全；`latest.tag` 与文件名结构不得改动。
- **`site/downloads_github.json` 与 `site/index.html`、`site/faq.html` 等路径受 `tests/contracts.test.mjs` 保护**，不得删除或改名。
- **四个下载渠道**：`github`、`ghproxy`（gh-proxy.com 大陆镜像，首页 JS 派生）、`gdrive`、`baidu`。
- **Analytics Engine 单次 `writeDataPoint` 上限**：20 blobs / 20 doubles / 1 index，blobs 合计 ≤16KB，单次 invocation ≤250 点。
- **`/t` 是公开端点，不做身份认证**（开源客户端藏不住密钥）。只做字段白名单校验与速率限制。

---

## Phase A — Worker 后端（仓库：`SuperPicky-Site`）

### Task 1: Worker 骨架与静态资源透传

这是全计划风险最高的一步：站点将从「纯静态、不可能挂」变成「有脚本、脚本错即整站挂」。本 Task 只做透传骨架，不加任何统计逻辑，先把「加了 Worker 也不会挂」这件事立住。

**Files:**
- Create: `src/index.js`
- Modify: `wrangler.jsonc`（新增 `main` 与 `analytics_engine_datasets`）
- Test: `tests/worker-routing.test.mjs`

**Interfaces:**
- Produces: `export default { fetch(request, env, ctx) }`；内部导出供测试的纯函数 `routeOf(pathname) -> 'dl' | 'telemetry' | 'stats' | 'assets'`

- [ ] **Step 1: 写失败测试**

创建 `tests/worker-routing.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { routeOf } from '../src/index.js';

describe('routeOf', () => {
  it('把 /dl/ 前缀识别为下载跳转', () => {
    expect(routeOf('/dl/mac_arm64-v4.5.0-baidu')).toBe('dl');
  });

  it('把 /t 识别为遥测端点', () => {
    expect(routeOf('/t')).toBe('telemetry');
  });

  it('把 /stats 识别为看板', () => {
    expect(routeOf('/stats')).toBe('stats');
  });

  it('其余一律走静态资源', () => {
    expect(routeOf('/')).toBe('assets');
    expect(routeOf('/downloads.html')).toBe('assets');
    expect(routeOf('/downloads_github.json')).toBe('assets');
    // /t 必须精确匹配，不能吃掉 /tutorial-4.2.1.html
    expect(routeOf('/tutorial-4.2.1.html')).toBe('assets');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/Documents/JamesAPPS/SuperPicky-Site && npx vitest run tests/worker-routing.test.mjs`
Expected: FAIL，`Failed to resolve import "../src/index.js"`

- [ ] **Step 3: 写最小实现**

创建 `src/index.js`：

```javascript
/**
 * superpicky.app 的 Worker 入口。
 *
 * 本站此前是纯静态 assets 部署（wrangler.jsonc 只有 assets，没有 main），
 * 那种形态下站点不可能因为代码出错而挂掉。加入本文件后这条保证消失，
 * 因此这里的第一原则是：**统计逻辑永远不能影响页面返回**。
 *
 * 三道保险：
 *   1. 静态资源先取到 response，再记账；
 *   2. 记账走 ctx.waitUntil()，不阻塞响应；
 *   3. 记账函数内部 try/catch 全吞，任何失败都不触碰 response。
 * 最坏情况是丢统计，不是丢站点。
 *
 * Entry Worker for superpicky.app. Analytics must never affect the response:
 * fetch assets first, log via ctx.waitUntil(), swallow every logging error.
 */

/**
 * 判断请求路径归属哪条路由。
 *
 * 抽成纯函数是为了能在不启动 Workers 运行时的前提下单测——本仓库的
 * vitest 是普通 node 环境，没有 workers pool。
 *
 * @param {string} pathname URL 的 path 部分
 * @returns {'dl'|'telemetry'|'stats'|'assets'} 路由标识
 */
export function routeOf(pathname) {
  if (pathname.startsWith('/dl/')) return 'dl';
  if (pathname === '/t') return 'telemetry';
  if (pathname === '/stats') return 'stats';
  return 'assets';
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    switch (routeOf(url.pathname)) {
      case 'dl':
      case 'telemetry':
      case 'stats':
        // Task 3 / 5 / 6 接管；在此之前一律 404，避免半成品路由返回 200。
        return new Response('Not Found', { status: 404 });
      default:
        return env.ASSETS.fetch(request);
    }
  }
};
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/worker-routing.test.mjs`
Expected: PASS（4 个用例）

- [ ] **Step 5: 改 wrangler.jsonc**

在 `wrangler.jsonc` 中 `"compatibility_date"` 之后加入 `main`，并在文件末尾 `"observability"` 之前加入数据集绑定。`routes`、`workers_dev` 保持原样不动；`assets` 块**只允许新增下面两个字段，既有的 `directory` / `not_found_handling` 及其注释一字不动**：

```jsonc
    // 在 Worker 脚本里访问静态资源必须显式声明这个绑定，
    // 否则 env.ASSETS 是 undefined，所有走到 Worker 的请求直接 500。
    // Required for env.ASSETS.fetch(); without it env.ASSETS is undefined.
    "binding": "ASSETS",

    // 默认是「静态资源优先」：命中文件的请求由资源层直接返回，**根本不经过
    // Worker 脚本**。那样一来页面访问统计只能记到 404，正常页面一次都记不到。
    // 故显式改为 Worker 优先，但用负规则把 css/js 与机器读取的文件排除掉——
    //   1. 只有页面文档才算「访问」，css/js 是同一次访问的附属请求；
    //   2. 更要紧的是计费：run_worker_first 命中的请求算 Worker 调用，
    //      免费额度 10 万/天，**超出后返回 429 而不是回退到静态资源**——
    //      也就是整站挂掉，不是丢统计。排除后约等于「1 次访问 1 次调用」，
    //      余量为 10 万页面浏览/天。
    // 负规则语义见 wrangler config-schema：「matches to negative rules will
    // go to the Asset Worker」。
    // Worker-first so pageviews are actually observable, minus sub-resources:
    // run_worker_first requests are billable and 429 (not asset fallback)
    // once the daily free tier is exhausted.
    "run_worker_first": [
      "/*",
      "!/css/*",
      "!/js/*",
      "!/robots.txt",
      "!/sitemap.xml",
      "!/downloads_github.json"
    ],
```

```jsonc
  // 静态站自此有了 Worker 入口。加 main 之后本站不再是「不可能挂」的纯 assets
  // 部署——src/index.js 的第一原则就是不让统计逻辑碰到 response。
  // Adding main turns this from an assets-only deploy into a script + assets
  // binding; src/index.js must never let analytics affect the response.
  "main": "src/index.js",
```

```jsonc
  // 访问统计/下载统计/app 心跳共用的数据集。
  // 免费额度 10 万数据点/天写入、1 万次查询/天，保留 3 个月。
  // Shared dataset for pageviews, downloads and app heartbeats.
  "analytics_engine_datasets": [
    { "binding": "STATS", "dataset": "superpicky_stats" }
  ],
```

- [ ] **Step 6: 全量测试 + 本地起 Worker 冒烟**

Run: `npx vitest run`
Expected: 全部 PASS（含既有的 contracts/links/r2key/rewrite/sitemap 测试）

Run: `npx wrangler dev --port 8788`
在另一个终端验证三件事：

```bash
curl -s -o /dev/null -w "首页 %{http_code}\n"        http://127.0.0.1:8788/
curl -s -o /dev/null -w "归档页 %{http_code}\n"      http://127.0.0.1:8788/downloads.html
curl -s -o /dev/null -w "归档页(规范化) %{http_code}\n" http://127.0.0.1:8788/downloads
curl -s -o /dev/null -w "死链 %{http_code}\n"        http://127.0.0.1:8788/no-such-page
```

Expected: `首页 200` / `归档页 307` / `归档页(规范化) 200` / **`死链 404`**

**`/downloads.html` 返回 307 是本站既有行为，不是缺陷**：Workers 静态资源的 `html_handling` 默认会把 `foo.html` 规范化跳转到 `/foo`。已用「移除 `main` 的纯 assets 基线」对照确认改动前后一致。

- [ ] **Step 7: 验证 404 页内容仍是自有 404 而非首页**

Run: `curl -s http://127.0.0.1:8788/no-such-page | head -20`
Expected: 输出 `site/404.html` 的内容。

**这一步不能跳过。** `not_found_handling: "404-page"` 是此前专门为 SEO 踩坑后定下的行为（SPA 模式会让死链以 200 被当作首页重复内容收录）。加 `main` 后 `env.ASSETS.fetch()` 是否仍继承该配置必须实测，不能靠推断。若此处返回的是首页内容或状态码是 200，**停止本计划并报告**——需要在 Worker 内显式处理 404 回退。

- [ ] **Step 8: 提交**

```bash
cd ~/Documents/JamesAPPS/SuperPicky-Site
git add src/index.js wrangler.jsonc tests/worker-routing.test.mjs
git commit -m "feat(worker): 新增 Worker 入口与路由骨架，静态资源原样透传

站点从 assets-only 变为 script + assets binding，为统计功能铺路。
本次不含任何统计逻辑，先确认加了 Worker 后 404 行为与静态透传不变。"
```

---

### Task 2: 页面访问统计

**Files:**
- Create: `src/logging.js`
- Modify: `src/index.js`
- Test: `tests/worker-logging.test.mjs`

**Interfaces:**
- Consumes: Task 1 的 `routeOf`
- Produces:
  - `isBot(userAgent: string|null) -> boolean`
  - `refererHost(referer: string|null, selfHost: string) -> string`（站内跳转返回 `'internal'`，无来源或畸形返回 `'direct'`）
  - `safeWrite(env, point: {indexes: string[], blobs: (string|null)[], doubles: number[]}) -> void`（吞掉一切失败，Task 3 与 Task 5 都依赖它）
  - `logPageview(request, env) -> void`（同步返回；`ctx.waitUntil` 由 `index.js` 的调用方负责，不在本函数内）

- [ ] **Step 1: 写失败测试**

创建 `tests/worker-logging.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { isBot, refererHost, safeWrite } from '../src/logging.js';

describe('isBot', () => {
  it('识别常见爬虫', () => {
    expect(isBot('Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)')).toBe(true);
    expect(isBot('Baiduspider/2.0')).toBe(true);
    expect(isBot('curl/8.4.0')).toBe(true);
  });

  it('不误伤真实浏览器', () => {
    expect(isBot('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36')).toBe(false);
  });

  it('空 UA 视为爬虫', () => {
    expect(isBot('')).toBe(true);
    expect(isBot(null)).toBe(true);
  });
});

describe('refererHost', () => {
  it('站内跳转标记为 internal', () => {
    expect(refererHost('https://superpicky.app/downloads.html', 'superpicky.app')).toBe('internal');
  });

  it('外站只取 host，不带路径（避免记录含隐私的完整 URL）', () => {
    expect(refererHost('https://www.google.com/search?q=superpicky', 'superpicky.app')).toBe('www.google.com');
  });

  it('无来源标记为 direct', () => {
    expect(refererHost('', 'superpicky.app')).toBe('direct');
    expect(refererHost(null, 'superpicky.app')).toBe('direct');
  });

  it('畸形 referer 不抛异常', () => {
    expect(refererHost('not a url', 'superpicky.app')).toBe('direct');
  });
});

describe('safeWrite', () => {
  it('binding 抛异常时不向外传播', () => {
    const brokenEnv = { STATS: { writeDataPoint() { throw new Error('AE down'); } } };
    expect(() => safeWrite(brokenEnv, { indexes: ['x'], blobs: [], doubles: [1] })).not.toThrow();
  });

  it('binding 缺失时不抛异常', () => {
    expect(() => safeWrite({}, { indexes: ['x'], blobs: [], doubles: [1] })).not.toThrow();
  });

  it('binding 正常时确实写入', () => {
    const calls = [];
    const env = { STATS: { writeDataPoint(p) { calls.push(p); } } };
    safeWrite(env, { indexes: ['pageview'], blobs: ['/'], doubles: [1] });
    expect(calls).toHaveLength(1);
    expect(calls[0].indexes).toEqual(['pageview']);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/worker-logging.test.mjs`
Expected: FAIL，`Failed to resolve import "../src/logging.js"`

- [ ] **Step 3: 写实现**

创建 `src/logging.js`：

```javascript
/**
 * 统计写入的公共部分。
 *
 * 所有对 Analytics Engine 的写入都必须经由 safeWrite()，它保证任何失败
 * （binding 缺失、配额耗尽、运行时异常）都被吞掉，绝不向调用方传播。
 *
 * Shared analytics helpers. Every AE write goes through safeWrite(), which
 * swallows all failures so that logging can never break a response.
 */

/** 爬虫 UA 特征。命中即打标，不丢弃——留着可作对照，看板默认排除。 */
const BOT_PATTERNS = [
  'bot', 'spider', 'crawler', 'slurp', 'curl', 'wget',
  'python-requests', 'headlesschrome', 'phantomjs', 'monitoring'
];

/**
 * 判断 User-Agent 是否为爬虫/自动化客户端。
 *
 * @param {string|null|undefined} userAgent 请求的 User-Agent
 * @returns {boolean} 是爬虫或 UA 为空时返回 true
 */
export function isBot(userAgent) {
  if (!userAgent) return true;               // 空 UA 一律按爬虫计
  const ua = userAgent.toLowerCase();
  return BOT_PATTERNS.some((p) => ua.includes(p));
}

/**
 * 从 Referer 提取来源域名。
 *
 * 只取 host 不取路径：完整 URL 可能带搜索词等隐私信息，而回答「访客从哪来」
 * 只需要域名。站内跳转归一为 internal，避免把内部导航算成外部来源。
 *
 * @param {string|null|undefined} referer Referer 头
 * @param {string} selfHost 本站域名
 * @returns {string} 来源域名，或 'internal' / 'direct'
 */
export function refererHost(referer, selfHost) {
  if (!referer) return 'direct';
  try {
    const host = new URL(referer).hostname;
    return host === selfHost ? 'internal' : host;
  } catch {
    return 'direct';                          // 畸形 referer 不应导致记账失败
  }
}

/**
 * 写入一个数据点，吞掉一切失败。
 *
 * @param {object} env Worker 环境，需含 STATS binding
 * @param {{indexes: string[], blobs: (string|null)[], doubles: number[]}} point 数据点
 * @returns {void}
 */
export function safeWrite(env, point) {
  try {
    env?.STATS?.writeDataPoint(point);
  } catch {
    // 统计失败绝不影响业务响应，故意静默。
  }
}

/**
 * 记录一次页面访问。
 *
 * @param {Request} request 原始请求
 * @param {object} env Worker 环境
 * @returns {void}
 */
export function logPageview(request, env) {
  const url = new URL(request.url);
  safeWrite(env, {
    indexes: ['pageview'],
    blobs: [
      url.pathname,
      refererHost(request.headers.get('referer'), url.hostname),
      request.cf?.country || 'unknown',
      request.headers.get('sec-ch-ua-mobile') === '?1' ? 'mobile' : 'desktop',
      isBot(request.headers.get('user-agent')) ? 'bot' : 'human'
    ],
    doubles: [1]
  });
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/worker-logging.test.mjs`
Expected: PASS（10 个用例：isBot 3 + refererHost 4 + safeWrite 3）

- [ ] **Step 5: 接入 index.js**

修改 `src/index.js`：顶部加 `import { logPageview } from './logging.js';`，把 `default` 分支改为——

```javascript
      default: {
        // 先取到 response 再记账：即使 logPageview 出问题，response 也已经在手上。
        // Fetch the asset first so logging can never stand between the visitor
        // and the page.
        const response = await env.ASSETS.fetch(request);
        ctx.waitUntil(Promise.resolve().then(() => logPageview(request, env)));
        return response;
      }
```

- [ ] **Step 6: 全量测试 + 冒烟**

Run: `npx vitest run`
Expected: 全部 PASS

Run: `npx wrangler dev --port 8788`，另开终端：
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8788/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8788/no-such-page
```
Expected: `200` / `404`（记账接入后行为不变）

- [ ] **Step 7: 提交**

```bash
git add src/logging.js src/index.js tests/worker-logging.test.mjs
git commit -m "feat(worker): 记录页面访问，爬虫打标不丢弃

服务端记账：无 cookie、无 JS、不受拦截插件与第三方脚本可达性影响。
所有 AE 写入经 safeWrite 吞掉失败，先取 response 再记账。"
```

---

### Task 3: 下载出站跳转

**Files:**
- Create: `src/dl-map.json`（由 Task 4 的脚本生成；本 Task 先手工放入 v4.5.0 的 8 条作为起点）
- Create: `src/redirect.js`
- Modify: `src/index.js`
- Test: `tests/worker-redirect.test.mjs`

**Interfaces:**
- Consumes: Task 2 的 `safeWrite`、`isBot`、`refererHost`
- Produces:
  - `makeDownloadId(platform: string, version: string, channel: string) -> string`（形如 `mac_arm64-v4.5.0-baidu`）
  - `handleRedirect(url: URL, request: Request, env) -> Promise<Response>`（三个参数，不接 `ctx`——记账是同步的，无需 waitUntil）

**下载 id 的构成**：`<platform>-<version>-<channel>`。三者组合天然唯一，无需哈希，且人眼可读——出问题时看一眼 URL 就知道是哪条线。`platform` 取值 `mac_arm64` / `mac_intel` / `win_cpu` / `win_cuda`，与 `downloads_github.json` 的键一致；`channel` 取值 `github` / `ghproxy` / `gdrive` / `baidu`。

- [ ] **Step 1: 写失败测试**

创建 `tests/worker-redirect.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { makeDownloadId, handleRedirect } from '../src/redirect.js';

const envOf = (calls) => ({ STATS: { writeDataPoint: (p) => calls.push(p) } });
const reqOf = (ua = 'Mozilla/5.0 Chrome/120') =>
  new Request('https://superpicky.app/dl/x', { headers: { 'user-agent': ua } });

describe('makeDownloadId', () => {
  it('按 平台-版本-渠道 拼接', () => {
    expect(makeDownloadId('mac_arm64', 'v4.5.0', 'baidu')).toBe('mac_arm64-v4.5.0-baidu');
  });
});

describe('handleRedirect', () => {
  it('已知 id 返回 302 且 Location 是真实地址', async () => {
    const calls = [];
    const res = await handleRedirect(
      new URL('https://superpicky.app/dl/mac_arm64-v4.5.0-baidu'), reqOf(), envOf(calls)
    );
    expect(res.status).toBe(302);
    expect(res.headers.get('location')).toContain('pan.baidu.com');
  });

  it('已知 id 会写入一个 dl 数据点', async () => {
    const calls = [];
    await handleRedirect(
      new URL('https://superpicky.app/dl/mac_arm64-v4.5.0-baidu'), reqOf(), envOf(calls)
    );
    expect(calls).toHaveLength(1);
    expect(calls[0].indexes).toEqual(['dl']);
    expect(calls[0].blobs).toContain('baidu');
  });

  it('未知 id 返回 404 而非 500，且不写数据点', async () => {
    const calls = [];
    const res = await handleRedirect(
      new URL('https://superpicky.app/dl/nope'), reqOf(), envOf(calls)
    );
    expect(res.status).toBe(404);
    expect(calls).toHaveLength(0);
  });

  it('爬虫会被打标但仍然跳转（不能让爬虫看到与真人不同的站点结构）', async () => {
    const calls = [];
    const res = await handleRedirect(
      new URL('https://superpicky.app/dl/mac_arm64-v4.5.0-baidu'), reqOf('Googlebot/2.1'), envOf(calls)
    );
    expect(res.status).toBe(302);
    expect(calls[0].blobs).toContain('bot');
  });

  it('AE 写入失败不影响跳转', async () => {
    const broken = { STATS: { writeDataPoint() { throw new Error('AE down'); } } };
    const res = await handleRedirect(
      new URL('https://superpicky.app/dl/mac_arm64-v4.5.0-baidu'), reqOf(), broken
    );
    expect(res.status).toBe(302);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/worker-redirect.test.mjs`
Expected: FAIL，`Failed to resolve import "../src/redirect.js"`

- [ ] **Step 3: 手工建立起点映射表**

创建 `src/dl-map.json`，内容取自 `site/downloads_github.json` 的 v4.5.0 区块（Task 4 将由脚本生成全量）：

```json
{
  "mac_arm64-v4.5.0-github": { "url": "https://github.com/jamesphotography/SuperPicky/releases/download/v4.5.0/SuperPicky_v4.5.0_arm64_6ff3387.dmg", "channel": "github", "version": "v4.5.0", "platform": "mac_arm64" },
  "mac_arm64-v4.5.0-gdrive": { "url": "https://drive.google.com/file/d/1luvGjAixIWuxv3H4_QcP9YGKLw7LxRzi/view?usp=sharing", "channel": "gdrive", "version": "v4.5.0", "platform": "mac_arm64" },
  "mac_arm64-v4.5.0-baidu": { "url": "https://pan.baidu.com/s/1T-_pLnIdOcvgr-cgz-ZcNw?pwd=3fyq", "channel": "baidu", "version": "v4.5.0", "platform": "mac_arm64" },
  "mac_intel-v4.5.0-github": { "url": "https://github.com/jamesphotography/SuperPicky/releases/download/v4.5.0/SuperPicky_v4.5.0_x86_64_6ff3387.dmg", "channel": "github", "version": "v4.5.0", "platform": "mac_intel" },
  "mac_intel-v4.5.0-gdrive": { "url": "https://drive.google.com/file/d/1JfGwX2n83ZotqIai4f64N_k5cC-4fToM/view?usp=sharing", "channel": "gdrive", "version": "v4.5.0", "platform": "mac_intel" },
  "mac_intel-v4.5.0-baidu": { "url": "https://pan.baidu.com/s/1FoXsSsBRpseujiZpbYpp5w?pwd=2d2k", "channel": "baidu", "version": "v4.5.0", "platform": "mac_intel" },
  "win_cpu-v4.5.0-github": { "url": "https://github.com/jamesphotography/SuperPicky/releases/download/v4.5.0/SuperPicky_Setup_Full_Win64_v4.5.0_6ff3387.exe", "channel": "github", "version": "v4.5.0", "platform": "win_cpu" },
  "win_cpu-v4.5.0-gdrive": { "url": "https://drive.google.com/file/d/1_AivYDY2xlOM0ZCj15FkC3sl8MXfsQev/view?usp=sharing", "channel": "gdrive", "version": "v4.5.0", "platform": "win_cpu" },
  "win_cpu-v4.5.0-baidu": { "url": "https://pan.baidu.com/s/1ZlKOEPbvj-RuNpnalul-Qw?pwd=dwu4", "channel": "baidu", "version": "v4.5.0", "platform": "win_cpu" },
  "win_cuda-v4.5.0-gdrive": { "url": "https://drive.google.com/file/d/1syeTJX550u6mHXV3OTrW-xplNLVqkBeg/view?usp=sharing", "channel": "gdrive", "version": "v4.5.0", "platform": "win_cuda" },
  "win_cuda-v4.5.0-baidu": { "url": "https://pan.baidu.com/s/1HqxaUjBNLZbXjMncsnwrqw?pwd=8n2q", "channel": "baidu", "version": "v4.5.0", "platform": "win_cuda" }
}
```

- [ ] **Step 4: 写实现**

创建 `src/redirect.js`：

```javascript
/**
 * 下载出站跳转与计数。
 *
 * 三个网盘渠道都不提供下载统计（Google Drive 的下载事件只存在于 Workspace
 * 企业版审计日志，百度网盘没有开放 API），因此改为在链接离开本站的那一刻
 * 自己记一笔，四个渠道由此获得统一口径。
 *
 * 附带收益：网盘链接自此可替换——换链接只改 dl-map.json，已经发出去的
 * /dl/<id> 地址依然有效，不必重发公告。
 *
 * Outbound download redirect + counting. Netdisk channels expose no download
 * stats, so we count at the moment the link leaves the site.
 */
import DL_MAP from './dl-map.json';
import { safeWrite, isBot, refererHost } from './logging.js';

/**
 * 拼出下载 id。
 *
 * 平台/版本/渠道三者组合天然唯一，故不做哈希——人眼可读，排查时看一眼
 * URL 就知道是哪条线。
 *
 * @param {string} platform mac_arm64 / mac_intel / win_cpu / win_cuda
 * @param {string} version 形如 v4.5.0
 * @param {string} channel github / ghproxy / gdrive / baidu
 * @returns {string} 下载 id
 */
export function makeDownloadId(platform, version, channel) {
  return `${platform}-${version}-${channel}`;
}

/**
 * 处理 /dl/<id>：记一笔后 302 到真实下载地址。
 *
 * @param {URL} url 请求 URL
 * @param {Request} request 原始请求
 * @param {object} env Worker 环境
 * @returns {Promise<Response>} 302 跳转，或未知 id 时 404
 */
export async function handleRedirect(url, request, env) {
  // 解码失败必须先于查表返回 404。畸形百分号编码（如 /dl/100%request）会让
  // decodeURIComponent 抛 URIError，不捕获就是公开端点上的未捕获异常。
  // Decode failures 404 before the lookup: a lone % throws URIError.
  let id;
  try {
    id = decodeURIComponent(url.pathname.slice('/dl/'.length));
  } catch {
    return new Response('Unknown download id', { status: 404 });
  }

  // 必须用自有属性检查，不能直接 DL_MAP[id]。DL_MAP 是 JSON 导入的普通对象，
  // `__proto__`/`constructor`/`toString` 等原型链键取到的是 Object.prototype
  // 上的成员，全是**真值**，会绕过下面的 404 守卫，最终以 entry.url === undefined
  // 走到 Response.redirect 抛 TypeError——一个随手可猜的公开路径上的 500。
  // 且 hasOwnProperty 必须从 Object.prototype 上取，不能写成 DL_MAP.hasOwnProperty(id)，
  // 否则一个名为 hasOwnProperty 的表项就能把守卫本身顶掉。
  // Own-property check is mandatory: prototype-chain keys are truthy and would
  // slip past the guard below. Call hasOwnProperty off Object.prototype so a
  // map entry named "hasOwnProperty" cannot subvert the guard itself.
  const entry = Object.prototype.hasOwnProperty.call(DL_MAP, id) ? DL_MAP[id] : undefined;

  // 未知 id 返回 404 而不是跳首页：坏链接应当明确暴露，
  // 静默跳首页会让「链接写错了」这件事永远不被发现。
  // 这条 return 必须在下面的 safeWrite 之前——否则非法 id 会先写出一条
  // 三字段全 undefined 的垃圾数据点再崩溃。
  if (!entry) return new Response('Unknown download id', { status: 404 });

  safeWrite(env, {
    indexes: ['dl'],
    blobs: [
      entry.channel,
      entry.version,
      entry.platform,
      refererHost(request.headers.get('referer'), url.hostname),
      isBot(request.headers.get('user-agent')) ? 'bot' : 'human'
    ],
    doubles: [1]
  });

  return Response.redirect(entry.url, 302);
}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `npx vitest run tests/worker-redirect.test.mjs`
Expected: PASS（6 个用例）

- [ ] **Step 6: 接入 index.js**

`src/index.js` 顶部加 `import { handleRedirect } from './redirect.js';`，把 `case 'dl':` 改为：

```javascript
      case 'dl':
        return handleRedirect(url, request, env);
```

（`case 'telemetry':` 与 `case 'stats':` 仍返回 404，由 Task 5 / 6 接管。）

- [ ] **Step 7: 全量测试 + 冒烟**

Run: `npx vitest run`
Expected: 全部 PASS

Run: `npx wrangler dev --port 8788`，另开终端：
```bash
curl -s -o /dev/null -w "已知 id %{http_code} -> %{redirect_url}\n" http://127.0.0.1:8788/dl/mac_arm64-v4.5.0-baidu
curl -s -o /dev/null -w "未知 id %{http_code}\n" http://127.0.0.1:8788/dl/nope
```
Expected: `已知 id 302 -> https://pan.baidu.com/...` / `未知 id 404`

- [ ] **Step 8: 提交**

```bash
git add src/redirect.js src/dl-map.json src/index.js tests/worker-redirect.test.mjs
git commit -m "feat(worker): 下载出站跳转与四渠道统一计数

网盘渠道不提供下载统计，改为在链接离开本站时自行记账。
附带收益：网盘链接自此可替换，旧 /dl/ 地址长期有效。"
```

---

### Task 4: 批量改写站内下载链接

71 条链接不可手工修改。本仓库既有的改写脚本（`scripts/rewrite-asset-urls.mjs`）确立了一条约定：**默认 dry-run，只报告不写盘**，因为批量正则改写内容文件曾经写坏过文件。本脚本沿用该约定。

**Files:**
- Create: `scripts/build-dl-map.mjs`
- Modify: `site/downloads.html`（71 条链接）、`site/downloads_github.json`（`drive` / `baidu` 两块）、`site/index.html`（首页动态卡片的 JS）
- Modify: `src/dl-map.json`（由脚本生成全量覆盖 Task 3 的手工起点）
- Test: `tests/dl-map.test.mjs`

**Interfaces:**
- Consumes: Task 3 的 `makeDownloadId` 的 id 规则
- Produces: `src/dl-map.json` 全量映射；`scripts/build-dl-map.mjs`（`--apply` 才写盘）

**改写范围与依据**：
- `site/downloads.html`：归档表。每个 `<tr>` 的 `<td class="version-cell">` 给出版本；`<td class="platform-cell">` 的列序对应 `<thead>` 的 `macOS (Apple Silicon)` / `macOS (Intel)` / `Windows (CPU)` / `Windows (CUDA)`，依次映射为 `mac_arm64` / `mac_intel` / `win_cpu` / `win_cuda`；渠道由 `<a class="dl-link">` 的 host 判定。
- `site/downloads_github.json`：只改 `latest.drive` 与 `latest.baidu` 的值为 `/dl/<id>`。**`latest.tag` 与 `latest.files` 一律不动**——`tag` 是已发行应用读取的契约（`tools/site_version.py`），`files` 是首页 JS 拼 GitHub 直链的来源。
- `site/index.html`：首页卡片的 JS 从 `files` 拼 `GH_BASE + 文件名`（github 渠道）和 `CN_PROXY_PREFIX + 直链`（ghproxy 渠道）。把这两处改为拼 `/dl/<platform>-<tag>-github` 与 `/dl/<platform>-<tag>-ghproxy`；`driveMap` / `baiduMap` 因 JSON 已改写而自动跟随。

- [ ] **Step 1: 写失败测试**

创建 `tests/dl-map.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const map = JSON.parse(readFileSync(ROOT + 'src/dl-map.json', 'utf8'));
const downloadsHtml = readFileSync(ROOT + 'site/downloads.html', 'utf8');
const downloadsJson = JSON.parse(readFileSync(ROOT + 'site/downloads_github.json', 'utf8'));

describe('dl-map 完整性', () => {
  it('每条记录都有四个必需字段且 channel 合法', () => {
    const channels = new Set(['github', 'ghproxy', 'gdrive', 'baidu']);
    for (const [id, e] of Object.entries(map)) {
      expect(e.url, `${id} 缺 url`).toMatch(/^https:\/\//);
      expect(channels.has(e.channel), `${id} channel 非法: ${e.channel}`).toBe(true);
      expect(e.version, `${id} 缺 version`).toBeTruthy();
      expect(e.platform, `${id} 缺 platform`).toBeTruthy();
    }
  });

  it('id 与其字段自洽（id 就是 平台-版本-渠道）', () => {
    for (const [id, e] of Object.entries(map)) {
      expect(id).toBe(`${e.platform}-${e.version}-${e.channel}`);
    }
  });

  it('跳转目标只指向白名单主机（防开放重定向）', () => {
    const allowed = ['github.com', 'gh-proxy.com', 'drive.google.com', 'pan.baidu.com'];
    for (const [id, e] of Object.entries(map)) {
      const host = new URL(e.url).hostname;
      expect(allowed.some((a) => host === a || host.endsWith('.' + a)), `${id} 主机不在白名单: ${host}`).toBe(true);
    }
  });
});

describe('站内链接已全部改写', () => {
  it('归档页不再有裸的网盘/GitHub 下载直链', () => {
    const bare = downloadsHtml.match(
      /href="https:\/\/(github\.com\/jamesphotography\/SuperPicky\/releases\/download|drive\.google\.com|pan\.baidu\.com)[^"]*"/g
    );
    expect(bare, `仍有 ${bare?.length ?? 0} 条未改写`).toBeNull();
  });

  it('归档页所有 dl-link 都指向 /dl/ 且 id 在映射表中', () => {
    const ids = [...downloadsHtml.matchAll(/href="\/dl\/([^"]+)"/g)].map((m) => m[1]);
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) expect(map[id], `归档页引用了不存在的 id: ${id}`).toBeDefined();
  });

  it('downloads_github.json 的 drive/baidu 已改写，tag 与 files 未被动过', () => {
    for (const url of Object.values(downloadsJson.latest.drive)) expect(url).toMatch(/^\/dl\//);
    for (const url of Object.values(downloadsJson.latest.baidu)) expect(url).toMatch(/^\/dl\//);
    expect(downloadsJson.latest.tag).toBe('v4.5.0');
    expect(downloadsJson.latest.files.mac_arm64).toMatch(/\.dmg$/);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/dl-map.test.mjs`
Expected: FAIL——「仍有 71 条未改写」以及 `downloads_github.json` 的 drive/baidu 仍是绝对地址。

- [ ] **Step 3: 写生成脚本**

创建 `scripts/build-dl-map.mjs`。

> **下方脚本有四个缺陷，实施时已修正（site 仓库 commit `7c5baa2`），此处保留原文并标注，以免重跑时重新引入：**
>
> 1. **非幂等且重跑即毁灭**——脚本从 `downloads.html` 提取绝对 URL 来建表，但第一次 `--apply` 之后页面里已经没有绝对 URL 了；再跑一次会生成一张空表，把 70 条线上 `/dl/` 链接**全部变成 404**。修法：以既有映射表为种子累加，并在写盘前自检、不通过则非零退出。
> 2. **版本号未归一**——`v4.3.0 LTS` 这类带空格的版本会产出含字面空格的 id，并把统计里的 `version` 维度劈成两半。
> 3. **id 方案在同格双包时碰撞**——v4.3.0 的 `Windows (CPU)` 单元格里有 Full 与 Lite 两个 GitHub 包，同一个 `win_cpu-<版本>-github` id 会让 Lite 静默覆盖 Full。需要额外的 `win_cpu_lite` 平台键。
> 4. **`github.com` 判定过宽**——页脚的仓库链接也会被当成下载链接改掉。须限定为 `/releases/download/` 路径。
>
> 另：正文写的「71 条链接」实为 **70** 条（另有 5 条夸克网盘链接不在白名单内，见下）。

```javascript
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * 从 site/downloads.html 与 site/downloads_github.json 提取全部下载链接，
 * 生成 src/dl-map.json，并把站内链接改写为 /dl/<id>。
 *
 *   node scripts/build-dl-map.mjs            # dry-run，只报告
 *   node scripts/build-dl-map.mjs --apply    # 真正写回
 *
 * 默认不写盘：批量正则改写内容文件曾经写坏过文件，先看报告再动手。
 * 这条约定与 scripts/rewrite-asset-urls.mjs 一致。
 */
const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SITE = join(ROOT, 'site');
const APPLY = process.argv.includes('--apply');

/** thead 列序 → downloads_github.json 的平台键。两处必须保持一致。 */
const PLATFORM_BY_COLUMN = ['mac_arm64', 'mac_intel', 'win_cpu', 'win_cuda'];

/** 下载主机 → 渠道名。白名单之外的 host 不改写（例如文档链接）。 */
function channelOf(url) {
  const host = new URL(url).hostname;
  if (host === 'github.com') return 'github';
  if (host === 'gh-proxy.com') return 'ghproxy';
  if (host === 'drive.google.com') return 'gdrive';
  if (host === 'pan.baidu.com') return 'baidu';
  return null;
}

const map = {};
const report = [];

// ── 1) 解析归档表 ────────────────────────────────────────────────
let html = await readFile(join(SITE, 'downloads.html'), 'utf8');

// 按 <tr> 切块，每块里取 version-cell 与依次出现的 platform-cell。
for (const row of html.split('<tr>').slice(1)) {
  const version = row.match(/class="version-cell">\s*([^<]+?)\s*</)?.[1];
  if (!version) continue;                                   // thead 行没有 version-cell

  const cells = [...row.matchAll(/class="platform-cell">([\s\S]*?)<\/td>/g)];
  cells.forEach((cell, columnIndex) => {
    const platform = PLATFORM_BY_COLUMN[columnIndex];
    if (!platform) return;
    for (const m of cell[1].matchAll(/href="(https:\/\/[^"]+)"/g)) {
      const channel = channelOf(m[1]);
      if (!channel) continue;
      const id = `${platform}-${version}-${channel}`;
      map[id] = { url: m[1], channel, version, platform };
      report.push(`${id}\n    ${m[1]}`);
    }
  });
}

// ── 2) 补上首页动态卡片的 github / ghproxy 两条（JS 端拼接，HTML 里没有） ──
const json = JSON.parse(await readFile(join(SITE, 'downloads_github.json'), 'utf8'));
const tag = json.latest.tag;
const GH_BASE = 'https://github.com/jamesphotography/SuperPicky/releases/download/';
for (const [platform, filename] of Object.entries(json.latest.files)) {
  const direct = `${GH_BASE}${tag}/${filename}`;
  map[`${platform}-${tag}-github`] = { url: direct, channel: 'github', version: tag, platform };
  map[`${platform}-${tag}-ghproxy`] = { url: `https://gh-proxy.com/${direct}`, channel: 'ghproxy', version: tag, platform };
}

// ── 3) 改写归档表里的链接 ─────────────────────────────────────────
const byUrl = new Map(Object.entries(map).map(([id, e]) => [e.url, id]));
let rewritten = 0;
html = html.replace(/href="(https:\/\/[^"]+)"/g, (whole, url) => {
  const id = byUrl.get(url);
  if (!id) return whole;                                    // 非下载链接保持原样
  rewritten += 1;
  return `href="/dl/${id}"`;
});

// ── 4) 改写 downloads_github.json 的 drive / baidu ────────────────
// latest.tag 与 latest.files 一律不动：tag 是已发行应用读取的契约
// （tools/site_version.py），files 是首页 JS 拼 GitHub 直链的来源。
for (const block of ['drive', 'baidu']) {
  const channel = block === 'drive' ? 'gdrive' : 'baidu';
  for (const [platform, url] of Object.entries(json.latest[block])) {
    const id = byUrl.get(url) ?? `${platform}-${tag}-${channel}`;
    if (!map[id]) map[id] = { url, channel, version: tag, platform };
    json.latest[block][platform] = `/dl/${id}`;
  }
}

console.log(`映射表 ${Object.keys(map).length} 条；归档页改写 ${rewritten} 处`);
console.log(report.join('\n'));

if (!APPLY) {
  console.log('\n[dry-run] 未写盘。确认无误后加 --apply。');
  process.exit(0);
}

await writeFile(join(ROOT, 'src/dl-map.json'), JSON.stringify(map, null, 2) + '\n', 'utf8');
await writeFile(join(SITE, 'downloads.html'), html, 'utf8');
await writeFile(join(SITE, 'downloads_github.json'), JSON.stringify(json, null, 2) + '\n', 'utf8');
console.log('已写回 src/dl-map.json、site/downloads.html、site/downloads_github.json');
```

- [ ] **Step 4: 先跑 dry-run，人工核对报告**

Run: `node scripts/build-dl-map.mjs`
Expected: 打印约 80 条映射与 71 处改写。**逐项核对**：版本号是否正确、平台列是否对位（特别是缺 GitHub 链接的 `Windows (CUDA)` 列不能串位）、有没有把非下载链接误改。

若列对位有误（`platform-cell` 数量与 `PLATFORM_BY_COLUMN` 不匹配的历史行），修正脚本后重跑 dry-run，**不要带着错误的报告执行 `--apply`**。

- [ ] **Step 5: 执行改写**

Run: `node scripts/build-dl-map.mjs --apply`
Expected: 打印「已写回 ...」三个文件。

- [ ] **Step 6: 改首页 JS**

修改 `site/index.html` 中约 793 行开始的渲染函数：`GH_BASE + 文件名` 与 `CN_PROXY_PREFIX + 直链` 两处改为 `/dl/` 形式。在该 IIFE 内加入：

```javascript
        // 下载链接统一走 /dl/<id>，由 Worker 记一笔后 302 到真实地址。
        // 四个渠道（github/ghproxy/gdrive/baidu）由此获得统一口径——
        // 网盘那两个渠道本身不提供任何下载统计。
        // All download links go through /dl/<id> so the four channels share
        // one counting method; netdisks expose no stats of their own.
        function dlHref(platformKey, tag, channel) {
            return '/dl/' + platformKey + '-' + tag + '-' + channel;
        }
```

并把原先直接使用 `GH_BASE`/`CN_PROXY_PREFIX` 拼接的两处替换为 `dlHref(platformKey, rel.tag, 'github')` 与 `dlHref(platformKey, rel.tag, 'ghproxy')`。`driveMap` / `baiduMap` 取自已改写的 JSON，无需再动。

- [ ] **Step 7: 运行测试确认通过**

Run: `npx vitest run`
Expected: 全部 PASS，包括 `tests/dl-map.test.mjs` 的 6 个用例与既有的 `contracts` / `links` 测试。

若 `tests/links.test.mjs` 因链接变成站内相对路径而失败，检查它的断言逻辑——`/dl/<id>` 是站内路径但没有对应的静态文件，可能需要在该测试中把 `/dl/` 前缀列为已知的动态路由而非死链。

- [ ] **Step 8: 冒烟验证首页与归档页**

Run: `npx wrangler dev --port 8788`，浏览器打开 `http://127.0.0.1:8788/` 与 `http://127.0.0.1:8788/downloads.html`，各点一个下载按钮，确认跳到真实地址。

- [ ] **Step 9: 提交**

```bash
git add scripts/build-dl-map.mjs src/dl-map.json site/downloads.html site/downloads_github.json site/index.html tests/dl-map.test.mjs
git commit -m "feat(site): 站内下载链接统一走 /dl/<id>

归档页 71 条 + 首页动态卡片 + downloads_github.json 的 drive/baidu 全部改写。
latest.tag 与 latest.files 未动（前者是已发行应用读取的契约）。
脚本默认 dry-run，沿用 rewrite-asset-urls.mjs 的约定。"
```

---

### Task 5: App 遥测接收端点

**Files:**
- Create: `src/telemetry.js`
- Modify: `src/index.js`
- Test: `tests/worker-telemetry.test.mjs`

**Interfaces:**
- Consumes: Task 2 的 `safeWrite`
- Produces:
  - `validatePayload(body: unknown) -> {ok: true, data: object} | {ok: false, reason: string}`
  - `handleTelemetry(request, env) -> Promise<Response>`

**`/t` 的请求契约**（App 端 Task 8 必须与此完全一致）：

```json
{
  "v": 1,
  "id": "3f2a...（64 位十六进制，按日轮换）",
  "app_version": "4.6.0",
  "os": "Darwin",
  "arch": "arm64",
  "python_version": "3.13.5",
  "locale": "zh_CN",
  "events": ["install", "app_start", "heartbeat_weekly"]
}
```

字段全部为字符串（`events` 为字符串数组），超长截断，未知字段丢弃。**不做身份认证**——开源客户端藏不住密钥，任何人都能伪造数据。校验只为防止畸形内容污染数据集。

- [ ] **Step 1: 写失败测试**

创建 `tests/worker-telemetry.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { validatePayload, handleTelemetry } from '../src/telemetry.js';

const valid = {
  v: 1,
  id: 'a'.repeat(64),
  app_version: '4.6.0',
  os: 'Darwin',
  arch: 'arm64',
  python_version: '3.13.5',
  locale: 'zh_CN',
  events: ['app_start']
};

const postOf = (body) => new Request('https://superpicky.app/t', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: typeof body === 'string' ? body : JSON.stringify(body)
});

describe('validatePayload', () => {
  it('接受合法 payload', () => {
    expect(validatePayload(valid).ok).toBe(true);
  });

  it('拒绝缺字段的 payload', () => {
    const { v, ...rest } = valid;
    expect(validatePayload(rest).ok).toBe(false);
  });

  it('拒绝非法 id（长度或字符不符）', () => {
    expect(validatePayload({ ...valid, id: 'xyz' }).ok).toBe(false);
    expect(validatePayload({ ...valid, id: 'z'.repeat(64) }).ok).toBe(false);
  });

  it('拒绝空的或过长的 events', () => {
    expect(validatePayload({ ...valid, events: [] }).ok).toBe(false);
    expect(validatePayload({ ...valid, events: Array(50).fill('app_start') }).ok).toBe(false);
  });

  it('拒绝未知事件名，防止数据集被任意字符串污染', () => {
    expect(validatePayload({ ...valid, events: ['drop table'] }).ok).toBe(false);
  });

  it('截断超长字段而不是拒绝（旧客户端可能带奇怪 locale）', () => {
    const r = validatePayload({ ...valid, locale: 'x'.repeat(500) });
    expect(r.ok).toBe(true);
    expect(r.data.locale.length).toBeLessThanOrEqual(64);
  });
});

describe('handleTelemetry', () => {
  it('每个事件写一个数据点', async () => {
    const calls = [];
    const env = { STATS: { writeDataPoint: (p) => calls.push(p) } };
    const res = await handleTelemetry(postOf({ ...valid, events: ['install', 'app_start'] }), env);
    expect(res.status).toBe(204);
    expect(calls).toHaveLength(2);
    expect(calls[0].indexes).toEqual(['app']);
  });

  it('非 POST 一律 405', async () => {
    const res = await handleTelemetry(new Request('https://superpicky.app/t'), {});
    expect(res.status).toBe(405);
  });

  it('畸形 JSON 返回 400 且不写数据点', async () => {
    const calls = [];
    const env = { STATS: { writeDataPoint: (p) => calls.push(p) } };
    const res = await handleTelemetry(postOf('{not json'), env);
    expect(res.status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it('AE 写入失败仍返回 204（客户端不应因服务端问题而重试风暴）', async () => {
    const env = { STATS: { writeDataPoint() { throw new Error('AE down'); } } };
    const res = await handleTelemetry(postOf(valid), env);
    expect(res.status).toBe(204);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/worker-telemetry.test.mjs`
Expected: FAIL，`Failed to resolve import "../src/telemetry.js"`

- [ ] **Step 3: 写实现**

创建 `src/telemetry.js`：

```javascript
/**
 * App 匿名心跳接收端点。
 *
 * 安全边界：本端点**不做身份认证**。客户端是开源的，任何密钥都能从源码或
 * 安装包中取出，因此这是一个公开端点，任何人都可以伪造数据点。这是开源
 * 桌面客户端的固有属性（原 Countly 实现的 README_TELEMETRY.md 就 app key
 * 说明过同一件事）。校验只为防止畸形内容污染数据集。
 *
 * 由此，这套数字的定位是**趋势观察，而非可审计的精确指标**。
 *
 * Anonymous app heartbeat endpoint. Deliberately unauthenticated — an
 * open-source client cannot keep a secret. Validation exists to keep the
 * dataset clean, not to prove authenticity.
 */
import { safeWrite } from './logging.js';

/** 允许的事件名。白名单之外一律拒绝，避免数据集被任意字符串撑爆。 */
const ALLOWED_EVENTS = new Set(['install', 'app_start', 'heartbeat_weekly']);

/** 单个字符串字段的最大长度，超出截断。 */
const MAX_FIELD = 64;

/** 按日轮换 ID 的格式：sha256 十六进制。 */
const ID_PATTERN = /^[0-9a-f]{64}$/;

const REQUIRED_STRINGS = ['app_version', 'os', 'arch', 'python_version', 'locale'];

/**
 * 校验并归一化上报内容。
 *
 * @param {unknown} body 已解析的 JSON
 * @returns {{ok: true, data: object}|{ok: false, reason: string}} 校验结果
 */
export function validatePayload(body) {
  if (!body || typeof body !== 'object') return { ok: false, reason: 'not an object' };
  if (body.v !== 1) return { ok: false, reason: 'unsupported version' };
  if (typeof body.id !== 'string' || !ID_PATTERN.test(body.id)) {
    return { ok: false, reason: 'bad id' };
  }
  if (!Array.isArray(body.events) || body.events.length === 0 || body.events.length > 10) {
    return { ok: false, reason: 'bad events' };
  }
  if (!body.events.every((e) => typeof e === 'string' && ALLOWED_EVENTS.has(e))) {
    return { ok: false, reason: 'unknown event' };
  }

  const data = { v: 1, id: body.id, events: [...body.events] };
  for (const key of REQUIRED_STRINGS) {
    if (typeof body[key] !== 'string' || body[key].length === 0) {
      return { ok: false, reason: `missing ${key}` };
    }
    // 截断而非拒绝：老客户端可能带来意料之外的 locale/arch 取值，
    // 为此丢掉整条上报不值得。
    data[key] = body[key].slice(0, MAX_FIELD);
  }
  return { ok: true, data };
}

/**
 * 处理 POST /t。
 *
 * @param {Request} request 原始请求
 * @param {object} env Worker 环境
 * @returns {Promise<Response>} 204 成功 / 400 畸形 / 405 方法不允许
 */
export async function handleTelemetry(request, env) {
  if (request.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response('Bad Request', { status: 400 });
  }

  const result = validatePayload(body);
  if (!result.ok) return new Response('Bad Request', { status: 400 });

  const { data } = result;
  // 一个事件一个数据点，计数时无需再拆数组。
  // 单次 invocation 上限 250 点，这里最多 10 点。
  for (const event of data.events) {
    safeWrite(env, {
      indexes: ['app'],
      // python_version 必须在列。它是 REQUIRED_STRINGS 之一（缺失即拒收整个 payload），
      // 漏写等于「强制客户端上报、服务端做完校验、然后扔掉」——数据集永远无法按
      // Python 版本切分。新增字段一律追加在末尾，不得插入中间：app 的 blob 位序
      // 被看板查询按位置依赖。
      // python_version must be here: it is a REQUIRED_STRING, so omitting it means
      // forcing clients to send a field the server validates then discards.
      // Append new fields at the end only — dashboard queries depend on blob order.
      blobs: [data.app_version, data.os, data.arch, data.locale, event, data.id, data.python_version],
      doubles: [1]
    });
  }

  // 即便写入失败也回 204：客户端把非 2xx 当作可重试，会在服务端出问题时
  // 形成重试风暴。统计数据丢一点，远好过把自己打挂。
  return new Response(null, { status: 204 });
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/worker-telemetry.test.mjs`
Expected: PASS（10 个用例）

- [ ] **Step 5: 接入 index.js**

顶部加 `import { handleTelemetry } from './telemetry.js';`，把 `case 'telemetry':` 改为：

```javascript
      case 'telemetry':
        return handleTelemetry(request, env);
```

- [ ] **Step 6: 全量测试 + 冒烟**

Run: `npx vitest run`
Expected: 全部 PASS

Run: `npx wrangler dev --port 8788`，另开终端：
```bash
curl -s -o /dev/null -w "合法 %{http_code}\n" -X POST http://127.0.0.1:8788/t \
  -H 'content-type: application/json' \
  -d '{"v":1,"id":"'"$(printf 'a%.0s' {1..64})"'","app_version":"4.6.0","os":"Darwin","arch":"arm64","python_version":"3.13.5","locale":"zh_CN","events":["app_start"]}'
curl -s -o /dev/null -w "畸形 %{http_code}\n" -X POST http://127.0.0.1:8788/t -d 'garbage'
curl -s -o /dev/null -w "GET %{http_code}\n" http://127.0.0.1:8788/t
```
Expected: `合法 204` / `畸形 400` / `GET 405`

- [ ] **Step 7: 提交**

```bash
git add src/telemetry.js src/index.js tests/worker-telemetry.test.mjs
git commit -m "feat(worker): 新增 /t 接收 app 匿名心跳

字段白名单 + 事件白名单校验，一个事件一个数据点。
公开端点、不做认证（开源客户端藏不住密钥），故数字定位为趋势观察。"
```

---

### Task 6: 统计看板

**Files:**
- Create: `src/stats.js`
- Modify: `src/index.js`
- Test: `tests/worker-stats.test.mjs`

**Interfaces:**
- Consumes: 无（独立读取路径）
- Produces: `handleStats(request, env) -> Promise<Response>`；`buildQueries(dataset: string) -> Record<string, string>`

**认证**：`?token=<STATS_TOKEN>`，`STATS_TOKEN` 存为 Worker secret（`npx wrangler secret put STATS_TOKEN`）。查询 Analytics Engine 需要 `CF_ACCOUNT_ID` 与 `CF_API_TOKEN` 两个 secret。

- [ ] **Step 1: 写失败测试**

创建 `tests/worker-stats.test.mjs`：

```javascript
import { describe, it, expect } from 'vitest';
import { buildQueries, handleStats } from '../src/stats.js';

describe('buildQueries', () => {
  it('每条查询都限定数据集并带时间范围', () => {
    const qs = buildQueries('superpicky_stats');
    for (const [name, sql] of Object.entries(qs)) {
      expect(sql, `${name} 未限定数据集`).toContain('superpicky_stats');
      expect(sql, `${name} 未限定时间范围`).toMatch(/timestamp\s*>/i);
    }
  });

  it('覆盖三类事件', () => {
    const qs = buildQueries('superpicky_stats');
    expect(Object.keys(qs)).toEqual(
      expect.arrayContaining(['dailyActive', 'versionMix', 'pageviews', 'downloads'])
    );
  });

  it('活跃人数按日轮换 ID 去重而非直接计数', () => {
    expect(buildQueries('superpicky_stats').dailyActive).toMatch(/uniq|distinct/i);
  });
});

describe('handleStats 认证', () => {
  const env = { STATS_TOKEN: 'secret', CF_ACCOUNT_ID: 'acc', CF_API_TOKEN: 'tok' };

  it('无 token 返回 401', async () => {
    const res = await handleStats(new Request('https://superpicky.app/stats'), env);
    expect(res.status).toBe(401);
  });

  it('token 错误返回 401', async () => {
    const res = await handleStats(new Request('https://superpicky.app/stats?token=wrong'), env);
    expect(res.status).toBe(401);
  });

  it('未配置 STATS_TOKEN 时一律 401，不因缺配置而敞开', async () => {
    const res = await handleStats(new Request('https://superpicky.app/stats?token=anything'), {});
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/worker-stats.test.mjs`
Expected: FAIL，`Failed to resolve import "../src/stats.js"`

- [ ] **Step 3: 写实现**

创建 `src/stats.js`：

```javascript
/**
 * 统计看板。
 *
 * 查询 Analytics Engine 的 SQL API，外加实时拉一次 GitHub Releases 汇总
 * 下载量（GitHub 的历史下载数由 API 现成提供，无需自建存储）。
 *
 * 本轮只求出数，不做实时刷新与时间范围选择器——界面待有数据后再迭代。
 *
 * Stats dashboard: AE SQL API + a live GitHub Releases call.
 */

const GITHUB_RELEASES =
  'https://api.github.com/repos/jamesphotography/SuperPicky/releases?per_page=100';

/**
 * 构造看板用的 SQL。
 *
 * 活跃人数必须按日轮换 ID 去重（uniq），不能直接 count——同一个人一天内
 * 多次启动会产生多条 app_start。
 *
 * @param {string} dataset Analytics Engine 数据集名
 * @returns {Record<string, string>} 查询名 → SQL
 */
export function buildQueries(dataset) {
  return {
    // 日活：按日期 × 去重 ID
    dailyActive: `
      SELECT toDate(timestamp) AS day, uniq(blob6) AS people
      FROM ${dataset}
      WHERE index1 = 'app' AND blob5 = 'app_start' AND timestamp > now() - INTERVAL '30' DAY
      GROUP BY day ORDER BY day DESC`,

    // 版本分布：逐日 × 各版本的去重人数
    //
    // 必须 GROUP BY day。blob6 按日轮换，同一个人一周内会贡献 7 个不同的值，
    // 因此跨日 uniq(blob6) 数的是「人×天」对而非人数——任何活跃超过一天的
    // 用户都会被重复计入，而列名 people 会让读的人以为那是人数。
    // 按天分组后单日内 blob6 = 一个人，每行都是真实人数；顺带给出版本渗透
    // 曲线，正是发版后最该看的东西。
    // GROUP BY day is mandatory: blob6 rotates daily, so a cross-day
    // uniq(blob6) counts person-days, not people.
    versionMix: `
      SELECT toDate(timestamp) AS day, blob1 AS app_version, blob2 AS os, uniq(blob6) AS people
      FROM ${dataset}
      WHERE index1 = 'app' AND blob5 = 'app_start' AND timestamp > now() - INTERVAL '7' DAY
      GROUP BY day, app_version, os ORDER BY day DESC, people DESC`,

    // 官网访问：排除爬虫
    pageviews: `
      SELECT toDate(timestamp) AS day, sum(_sample_interval) AS views
      FROM ${dataset}
      WHERE index1 = 'pageview' AND blob5 = 'human' AND timestamp > now() - INTERVAL '30' DAY
      GROUP BY day ORDER BY day DESC`,

    // 下载跳转：按渠道
    downloads: `
      SELECT blob1 AS channel, blob2 AS version, sum(_sample_interval) AS clicks
      FROM ${dataset}
      WHERE index1 = 'dl' AND blob5 = 'human' AND timestamp > now() - INTERVAL '30' DAY
      GROUP BY channel, version ORDER BY clicks DESC`
  };
}

/**
 * 执行一条 AE SQL 查询。
 *
 * @param {string} sql 查询语句
 * @param {object} env Worker 环境
 * @returns {Promise<object>} 查询结果，失败时返回 {error}
 */
async function runQuery(sql, env) {
  try {
    const res = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}/analytics_engine/sql`,
      { method: 'POST', headers: { Authorization: `Bearer ${env.CF_API_TOKEN}` }, body: sql }
    );
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return await res.json();
  } catch (err) {
    return { error: String(err) };
  }
}

/**
 * 处理 GET /stats。
 *
 * @param {Request} request 原始请求
 * @param {object} env Worker 环境
 * @returns {Promise<Response>} JSON 结果，或 401
 */
export async function handleStats(request, env) {
  const token = new URL(request.url).searchParams.get('token');

  // 未配置 STATS_TOKEN 时一律拒绝——缺配置绝不能等于不设防。
  if (!env.STATS_TOKEN || token !== env.STATS_TOKEN) {
    return new Response('Unauthorized', { status: 401 });
  }

  const queries = buildQueries('superpicky_stats');
  const entries = await Promise.all(
    Object.entries(queries).map(async ([name, sql]) => [name, await runQuery(sql, env)])
  );

  // GitHub 下载量：现成数据，实时拉取不落库。
  let github = { error: 'not fetched' };
  try {
    const res = await fetch(GITHUB_RELEASES, {
      headers: { 'user-agent': 'superpicky-site-stats', accept: 'application/vnd.github+json' }
    });
    if (res.ok) {
      const releases = await res.json();
      github = {
        total: releases.reduce(
          (sum, r) => sum + r.assets.reduce((s, a) => s + a.download_count, 0), 0
        ),
        byRelease: releases.map((r) => ({
          tag: r.tag_name,
          published: r.published_at?.slice(0, 10),
          downloads: r.assets.reduce((s, a) => s + a.download_count, 0)
        }))
      };
    }
  } catch (err) {
    github = { error: String(err) };
  }

  return new Response(
    JSON.stringify({ ...Object.fromEntries(entries), github }, null, 2),
    { headers: { 'content-type': 'application/json; charset=utf-8' } }
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/worker-stats.test.mjs`
Expected: PASS（6 个用例）

- [ ] **Step 5: 接入 index.js 并配置 secret**

顶部加 `import { handleStats } from './stats.js';`，把 `case 'stats':` 改为：

```javascript
      case 'stats':
        return handleStats(request, env);
```

配置三个 secret（`CF_API_TOKEN` 需要 Account Analytics 读权限）：

```bash
npx wrangler secret put STATS_TOKEN
npx wrangler secret put CF_ACCOUNT_ID
npx wrangler secret put CF_API_TOKEN
```

- [ ] **Step 6: 全量测试**

Run: `npx vitest run`
Expected: 全部 PASS

- [ ] **Step 7: 提交并部署**

```bash
git add src/stats.js src/index.js tests/worker-stats.test.mjs
git commit -m "feat(worker): 新增 /stats 看板

AE SQL 查询四类指标 + 实时拉 GitHub Releases 下载量。
活跃人数按日轮换 ID 去重；缺 STATS_TOKEN 时一律 401。"
npx wrangler deploy
```

- [ ] **Step 8: 线上验证（部署后必做）**

```bash
curl -s -o /dev/null -w "首页 %{http_code}\n"   https://superpicky.app/
curl -s -o /dev/null -w "死链 %{http_code}\n"   https://superpicky.app/no-such-page
curl -s -o /dev/null -w "跳转 %{http_code}\n"   https://superpicky.app/dl/mac_arm64-v4.5.0-baidu
curl -s "https://superpicky.app/stats?token=<你设置的值>" | head -40
```

Expected: `首页 200` / **`死链 404`** / `跳转 302` / 看板返回 JSON。

同时验证 `workers.dev` 地址仍可访问（切换域名前唯一不依赖 DNS 的验证入口，`wrangler.jsonc` 里刻意保留了 `workers_dev: true`）。

---

## Phase B — App 端（仓库：`SuperPicky2026`）

### Task 7: 遥测开关并入 advanced_config

**Files:**
- Modify: `advanced_config.py`（`DEFAULT_CONFIG` 约 96-101 行区域；property 约 549-557 行区域）
- Test: `test_telemetry_config.py`（新建）

**Interfaces:**
- Produces: `AdvancedConfig.telemetry_enabled -> bool`、`AdvancedConfig.set_telemetry_enabled(value: bool) -> None`

现有 `telemetry_consent.json` 是独立 json，违反 `CLAUDE.md` 的 SSOT 规则（「advanced_config 是所有设置的唯一存储……禁止再引入独立 json」）。本 Task 把开关搬进 `advanced_config`。

- [ ] **Step 1: 写失败测试**

创建 `test_telemetry_config.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测开关的 advanced_config 契约测试。

注入 config_file 以隔离本机真实配置——测试写入用户真实
advanced_config.json 会静默改掉本机设置（见 CLAUDE.md 与既往教训）。

Contract tests for the telemetry toggle. config_file is injected so the
test never touches the user's real advanced_config.json.
"""
from pathlib import Path

import pytest

from advanced_config import AdvancedConfig


@pytest.fixture
def cfg(tmp_path: Path) -> AdvancedConfig:
    """返回一个写入临时目录的独立配置实例。/ Isolated config instance."""
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


def test_telemetry_defaults_to_enabled(cfg: AdvancedConfig) -> None:
    """默认开启（opt-out 策略）。/ Opt-out: enabled by default."""
    assert cfg.telemetry_enabled is True


def test_telemetry_can_be_disabled(cfg: AdvancedConfig) -> None:
    """可关闭并持久化。/ Can be turned off and persisted."""
    cfg.set_telemetry_enabled(False)
    assert cfg.telemetry_enabled is False
    cfg.save()

    reloaded = AdvancedConfig(config_file=cfg.config_file)
    assert reloaded.telemetry_enabled is False


def test_telemetry_setter_coerces_to_bool(cfg: AdvancedConfig) -> None:
    """setter 强制转 bool，避免 Qt 的 int 状态值直接落库。/ Coerce to bool."""
    cfg.set_telemetry_enabled(0)      # Qt.Unchecked
    assert cfg.telemetry_enabled is False
    cfg.set_telemetry_enabled(2)      # Qt.Checked
    assert cfg.telemetry_enabled is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/Documents/JamesAPPS/SuperPicky2026 && python3 -m pytest test_telemetry_config.py -v`
Expected: FAIL，`AttributeError: 'AdvancedConfig' object has no attribute 'telemetry_enabled'`

（`AdvancedConfig.__init__(config_file: Optional[str] = None)` 与实例属性 `self.config_file` 均已存在，见 `advanced_config.py:194` / `:210`，fixture 可直接如上使用。）

- [ ] **Step 3: 加 DEFAULT_CONFIG 字段**

在 `advanced_config.py` 的 `DEFAULT_CONFIG` 中，`"rescue_birdid_gate"` 之后、`"external_apps"` 之前插入：

```python
        # V4.6: 匿名使用统计 (spec: docs/specs/2026-08-21-usage-analytics-design.md)
        # 默认开启、设置中心可关；上报内容为版本/系统/架构/语言与按日轮换的
        # 匿名 ID，不含任何照片、路径或个人信息。
        # V4.6: Anonymous usage stats — opt-out, no photo/path/personal data.
        "telemetry_enabled": True,
```

- [ ] **Step 4: 加 property 与 setter**

在 `set_completion_sound_enabled` 之后插入：

```python
    @property
    def telemetry_enabled(self) -> bool:
        """
        返回是否上报匿名使用统计。

        返回:
        bool: True 表示上报（默认），False 表示完全不发起网络请求。

        Return whether anonymous usage stats are reported.

        Return:
        bool: True to report (default); False disables all network calls.
        """
        return self.config.get("telemetry_enabled", True)

    def set_telemetry_enabled(self, value: bool) -> None:
        """
        设置是否上报匿名使用统计。

        参数:
        value (bool): True 开启，False 关闭。强制转 bool，
                      以免 Qt 的 int 状态值（0/2）直接落库。

        Set whether anonymous usage stats are reported.

        Parameters:
        value (bool): Coerced to bool so Qt's int check states never persist.
        """
        self.config["telemetry_enabled"] = bool(value)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest test_telemetry_config.py -v`
Expected: PASS（3 个用例）

- [ ] **Step 6: 编译检查 + 回归**

Run: `python3 -m py_compile advanced_config.py`
Expected: 无输出

Run: `python3 -m pytest test_advanced_config.py -v 2>/dev/null || python3 -m pytest -k advanced_config -v`
Expected: 既有配置测试全绿

- [ ] **Step 7: 提交**

```bash
git add advanced_config.py test_telemetry_config.py
git commit -m "feat(config): 遥测开关并入 advanced_config

原 telemetry_consent.json 是独立 json，违反 CLAUDE.md 的设置 SSOT 规则。
默认 True（opt-out），setter 强制转 bool 以免 Qt int 状态值落库。"
```

---

### Task 8: 遥测投递层改造

**Files:**
- Modify: `app_user_stat/telemetry.py`
- Delete: `app_user_stat/consent_texts/`（整个目录）
- Test: `test_telemetry_send.py`（新建）

**Interfaces:**
- Consumes: Task 7 的 `AdvancedConfig.telemetry_enabled`；Task 5 的 `/t` 请求契约
- Produces:
  - `_daily_rotating_id(install_id: str, day: str) -> str`（sha256 十六进制，64 位）
  - `_build_request_payload(install_id: str, events: List[str]) -> Dict[str, Any]`
  - 端点常量 `_TELEMETRY_ENDPOINT = "https://superpicky.app/t"`

保留不动：后台线程投递、异常全吞、`telemetry_state.json` 持久化、7 天心跳节流、`_BOOTSTRAPPED` 幂等锁、`run()` 的 `try/finally` 回调保障。

- [ ] **Step 1: 写失败测试**

创建 `test_telemetry_send.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测投递层测试：按日轮换 ID 与上报内容契约。

Telemetry delivery tests: daily-rotating ID and payload contract.
"""
import re

from app_user_stat.telemetry import _build_request_payload, _daily_rotating_id


def test_daily_id_is_sha256_hex() -> None:
    """上报 ID 为 64 位十六进制，与 Worker 的校验正则一致。/ 64-hex id."""
    value = _daily_rotating_id("install-abc", "2026-08-21")
    assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_daily_id_is_stable_within_a_day() -> None:
    """同一天内稳定，否则算不出去重日活。/ Stable within one day."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-21")
    assert a == b


def test_daily_id_rotates_across_days() -> None:
    """跨日必须变化，这是「不构成持久标识符」的关键。/ Rotates daily."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-22")
    assert a != b


def test_daily_id_differs_between_installs() -> None:
    """不同安装不得碰撞，否则日活会被低估。/ Distinct per install."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-xyz", "2026-08-21")
    assert a != b


def test_payload_never_contains_the_local_install_id() -> None:
    """
    本地安装 ID 永不上报——这是整个匿名方案的立足点。

    The local install id must never appear in the payload.
    """
    payload = _build_request_payload("install-secret-value", ["app_start"])
    assert "install-secret-value" not in str(payload)


def test_payload_matches_worker_contract() -> None:
    """字段与 Worker 的 validatePayload 白名单一致。/ Matches /t contract."""
    payload = _build_request_payload("install-abc", ["install", "app_start"])
    assert payload["v"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["id"])
    assert payload["events"] == ["install", "app_start"]
    for key in ("app_version", "os", "arch", "python_version", "locale"):
        assert isinstance(payload[key], str) and payload[key]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_telemetry_send.py -v`
Expected: FAIL，`ImportError: cannot import name '_daily_rotating_id'`

- [ ] **Step 3: 实现按日轮换 ID 与新 payload**

在 `app_user_stat/telemetry.py` 中：顶部 `import` 区加 `import hashlib`；替换 Countly 相关常量为——

```python
# 自建端点。原 Countly Flex 实例（superpicky-*.flex.countly.com）的域名
# 已不存在，数月来所有打包版都在向它静默投递失败、零数据落地。
# 域名属第三方、拿不回来，故已发布版本的数据永久缺失，无兼容负担。
# Self-hosted endpoint; the former Countly Flex host no longer resolves.
_TELEMETRY_ENDPOINT = "https://superpicky.app/t"
_PAYLOAD_VERSION = 1
```

新增两个函数：

```python
def _daily_rotating_id(install_id: str, day: str) -> str:
    """
    由本地安装 ID 与日期派生出当日的上报 ID。

    参数:
    install_id (str): 本地安装 ID，仅存于 telemetry_state.json，永不上报。
    day (str): UTC 日期，格式 YYYY-MM-DD。

    返回:
    str: 64 位十六进制的 sha256 摘要。

    同一天内稳定（否则算不出去重日活），跨日必变（故不构成持久标识符，
    这正是「默认开启」得以成立的前提）。代价是算不了留存。

    Derive the day's reporting id from the local install id and the date.
    Stable within a day, rotates across days, so it is not a persistent
    identifier. The trade-off is that retention cannot be computed.

    Parameters:
    install_id (str): Local-only install id, never transmitted.
    day (str): UTC date as YYYY-MM-DD.

    Return:
    str: 64-char lowercase sha256 hex digest.
    """
    return hashlib.sha256(f"{install_id}:{day}".encode("utf-8")).hexdigest()


def _build_request_payload(install_id: str, events: List[str]) -> Dict[str, Any]:
    """
    构造 POST /t 的上报内容。

    参数:
    install_id (str): 本地安装 ID，只用于派生当日 ID，不进入返回值。
    events (List[str]): 事件名列表，取值须在 Worker 的白名单内
                        （install / app_start / heartbeat_weekly）。

    返回:
    Dict[str, Any]: 与 Worker 端 validatePayload 契约一致的字典。

    Build the POST /t payload. install_id is used only to derive the daily id
    and never appears in the result.

    Parameters:
    install_id (str): Local-only install id.
    events (List[str]): Event keys from the Worker's allow-list.

    Return:
    Dict[str, Any]: Payload matching the Worker's validatePayload contract.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    common = _build_common_fields()
    return {
        "v": _PAYLOAD_VERSION,
        "id": _daily_rotating_id(install_id, day),
        "app_version": common["app_version"],
        "os": common["os"],
        "arch": common["arch"],
        "python_version": common["python_version"],
        "locale": common["locale"],
        "events": list(events),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_telemetry_send.py -v`
Expected: PASS（6 个用例）

- [ ] **Step 5: 改投递与开关判定**

改 `_send_due_events`：改为 `POST` JSON 到 `_TELEMETRY_ENDPOINT`，`Content-Type: application/json`，body 为 `json.dumps(payload).encode("utf-8")`，保留既有的 `timeout=_REQUEST_TIMEOUT_SECONDS` 与全量异常吞掉。

改 `_TelemetryBootstrap.run()`：删掉 `_ensure_user_consent()` / `has_real_app_key` / `has_real_server_url` / `is_configured` 四个分支，改为单一判定——

```python
    def run(self) -> None:
        try:
            from advanced_config import AdvancedConfig

            if not AdvancedConfig().telemetry_enabled:
                _debug_log("telemetry skipped: disabled in settings")
                return

            _TelemetryClient().bootstrap()
        finally:
            # 这个 try/finally 必须原样保留。启动期弹窗（onboarding）挂在
            # on_ready 上（main.py:253），任何提前 return 都必须仍然触发它，
            # 否则新用户永远看不到 onboarding 且没有任何报错。
            # Keep this try/finally: onboarding hangs off on_ready.
            _invoke_callback(self._on_ready)
```

同步删除 `_ensure_user_consent`、`_show_consent_dialog`、`_load_consent_copy`、`_resolve_consent_language`、`_load_consent_state`、`_CONSENT_FILE_NAME`，以及 `CountlyConfig`、`_resolve_countly_config`、`_load_build_override` 及相关占位常量。`_TelemetryClient.__init__` 不再需要 config 参数。

- [ ] **Step 6: 加 on_ready 回归测试**

追加到 `test_telemetry_send.py`：

```python
def test_on_ready_fires_even_when_telemetry_disabled(monkeypatch) -> None:
    """
    遥测关闭时 on_ready 仍须触发，否则 onboarding 永不出现且无报错。

    on_ready must fire even when telemetry is off, or onboarding never shows.
    """
    import app_user_stat.telemetry as tm

    class _Off:
        telemetry_enabled = False

    monkeypatch.setattr(tm, "_schedule_on_qt_event_loop", lambda fn: False)
    monkeypatch.setitem(__import__("sys").modules, "advanced_config",
                        type("M", (), {"AdvancedConfig": lambda: _Off()}))
    tm._BOOTSTRAPPED = False

    fired = []
    tm.bootstrap_telemetry(parent=None, on_ready=lambda: fired.append(True))
    assert fired == [True]


def test_unreachable_endpoint_never_raises(monkeypatch) -> None:
    """
    端点不可达时不得抛异常——这正是 Countly 域名失效时的处境。

    当年那个死域名之所以数月无人察觉，是因为异常全被吞掉；吞异常本身是
    对的（统计不能拖垮启动），本测试守住这个行为不被「改成抛错好排查」。

    An unreachable endpoint must never raise: swallowing is correct here,
    since telemetry must never break startup.
    """
    import app_user_stat.telemetry as tm

    def _boom(*args, **kwargs):
        raise OSError("Could not resolve host")

    monkeypatch.setattr(tm.request, "urlopen", _boom)
    client = tm._TelemetryClient()
    state = {"device_id": "install-abc", "install_reported_at": None, "last_heartbeat_at": None}
    # 不应抛出任何异常
    client._send_due_events(state, ["app_start"])
```

Run: `python3 -m pytest test_telemetry_send.py -v`
Expected: PASS（8 个用例）

若 `_send_due_events` 的实参形状在 Step 5 改造后与此不同，按实际签名调整调用——**断言不变：不得抛异常**。

- [ ] **Step 7: 删除同意文案目录并自检**

```bash
git rm -r app_user_stat/consent_texts
python3 -m py_compile app_user_stat/telemetry.py
python3 -m app_user_stat.telemetry
```

Expected: 自检输出显示 `endpoint_url=https://superpicky.app/t`，`due_events` 正常，payload 预览中**不含**本地安装 ID。

- [ ] **Step 8: 提交**

```bash
git add app_user_stat/telemetry.py test_telemetry_send.py
git commit -m "feat(telemetry): 改投自建端点，匿名 ID 按日轮换

原 Countly 域名已不存在，数月零数据落地且静默失败，无兼容负担。
ID 改为 sha256(安装ID+日期)：同日稳定、跨日轮换，不构成持久标识符。
本地安装 ID 永不上报。同意流程改由 advanced_config.telemetry_enabled 控制。
run() 的 try/finally 原样保留——onboarding 挂在 on_ready 上。"
```

---

### Task 9: 设置中心开关与首启告知

**Files:**
- Modify: `ui/settings_center.py`（`_build_about_page`，约 2411 行起）
- Modify: `locales/zh_CN.json`、`locales/en_US.json`
- Test: `test_telemetry_settings_ui.py`（新建）

**Interfaces:**
- Consumes: Task 7 的 `telemetry_enabled` / `set_telemetry_enabled`

opt-out 不等于不告知。开关放「关于」页，并在其下方写明采集内容。

- [ ] **Step 1: 加 i18n 键**

`locales/zh_CN.json` 的 `settings` 段加入：

```json
    "telemetry_label": "发送匿名使用统计",
    "telemetry_desc": "仅发送版本号、操作系统、语言与一个每日变更的随机编号，用于了解有多少人在使用。不含任何照片、文件路径或个人信息。",
```

`locales/en_US.json` 同一位置加入：

```json
    "telemetry_label": "Send anonymous usage statistics",
    "telemetry_desc": "Sends only the app version, operating system, language, and a random ID that changes daily, so we can tell how many people use SuperPicky. No photos, file paths, or personal information are ever sent.",
```

- [ ] **Step 2: 写失败测试**

创建 `test_telemetry_settings_ui.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测开关的 i18n 与文案测试。

不构造 MainWindow——构造它会切换全局 i18n 语言，导致本地化断言假失败
（既往教训）。这里只校验键存在且两种语言都有。

i18n coverage for the telemetry toggle. Deliberately avoids constructing
MainWindow, which switches the global i18n language.
"""
import json
from pathlib import Path

import pytest

LOCALES = Path(__file__).parent / "locales"


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_keys_exist(filename: str) -> None:
    """两种语言都必须有开关文案，缺一个就是界面上的空标签。/ Both locales."""
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    assert "telemetry_label" in data["settings"]
    assert "telemetry_desc" in data["settings"]


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_desc_states_what_is_not_sent(filename: str) -> None:
    """
    说明文案必须写明「不含照片/路径/个人信息」。

    opt-out 默认开启的前提是说明必须到位；这条测试守住它不被简化掉。

    The description must state what is NOT collected — the premise of opt-out.
    """
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    desc = data["settings"]["telemetry_desc"].lower()
    assert ("照片" in desc or "photo" in desc)
    assert ("个人信息" in desc or "personal information" in desc)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest test_telemetry_settings_ui.py -v`
Expected: 若 Step 1 已完成则 PASS；若先写测试则 FAIL 于 `KeyError: 'telemetry_label'`。按 TDD 顺序应先写测试再加键——如已按 Step 1 加过，回退该改动确认测试确实会红，再加回。

- [ ] **Step 4: 在「关于」页加开关**

在 `ui/settings_center.py` 的 `_build_about_page` 中，版本与致谢信息之后、`addStretch` 之前插入：

```python
        # 匿名使用统计开关（默认开启，可关）。
        # 放在「关于」页而非「精选」页：它不影响任何处理结果，
        # 与版本/许可证一样属于「关于这个程序本身」的信息。
        # Anonymous usage stats toggle — belongs with version/license info
        # since it does not affect any processing result.
        self._telemetry_checkbox = QCheckBox(self.i18n.t("settings.telemetry_label"))
        self._telemetry_checkbox.setChecked(self.config.telemetry_enabled)
        self._telemetry_checkbox.setStyleSheet(self._checkbox_qss())
        self._telemetry_checkbox.stateChanged.connect(self._on_telemetry_changed)
        lay.addWidget(self._telemetry_checkbox)

        telemetry_hint = QLabel(self.i18n.t("settings.telemetry_desc"))
        telemetry_hint.setWordWrap(True)
        telemetry_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        lay.addWidget(telemetry_hint)
```

并在该类的槽函数区加入：

```python
    def _on_telemetry_changed(self, state: int) -> None:
        """
        切换匿名使用统计开关并落盘。

        参数:
        state (int): Qt 的勾选状态（0 未选 / 2 已选）。

        返回:
        None

        Toggle anonymous usage stats and persist immediately.

        Parameters:
        state (int): Qt check state (0 unchecked / 2 checked).

        Return:
        None
        """
        self.config.set_telemetry_enabled(bool(state))
        self.config.save()
```

**注意**：`lay`、`self.config`、`COLORS`、`self._checkbox_qss()` 的实际名称须与 `_build_about_page` 中既有代码一致；若该方法内布局变量名不是 `lay`，按实际名称调整。

- [ ] **Step 5: 运行测试并编译检查**

Run: `python3 -m pytest test_telemetry_settings_ui.py -v`
Expected: PASS（4 个用例）

Run: `python3 -m py_compile ui/settings_center.py`
Expected: 无输出

- [ ] **Step 6: 目视验证**

启动应用，打开设置中心 →「关于」页，确认：开关存在且默认勾选、说明文字换行正常、取消勾选后关闭再打开设置中心仍保持未勾选。

- [ ] **Step 7: 提交**

```bash
git add ui/settings_center.py locales/zh_CN.json locales/en_US.json test_telemetry_settings_ui.py
git commit -m "feat(ui): 设置中心「关于」页加匿名统计开关

opt-out 的前提是说明到位：开关下方写明采集内容与不采集内容，
并由测试守住「不含照片/个人信息」这句话不被简化掉。"
```

---

### Task 10: 清理 Countly 残留

**Files:**
- Delete: `scripts/prepare_telemetry_build.py`
- Modify: `.github/workflows/build-release.yml`（删除 L46-48 与 L197-199 两处注入步骤）
- Modify: `app_user_stat/README_TELEMETRY.md`（全文重写）
- Test: `test_no_countly_residue.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `test_no_countly_residue.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确认 Countly 残留已清除。

留着失效的注入步骤会让下一个人以为遥测仍走 Countly——正是这次
「配置齐全但数据为零」的误判来源。

Guard against Countly residue: stale wiring is what made the dead endpoint
go unnoticed for months.
"""
from pathlib import Path

ROOT = Path(__file__).parent


def test_workflow_has_no_countly_secrets() -> None:
    """CI 不再注入 Countly 凭据。/ No Countly secrets in CI."""
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert "COUNTLY_APP_KEY" not in workflow
    assert "COUNTLY_SERVER_URL" not in workflow
    assert "prepare_telemetry_build" not in workflow


def test_prepare_script_removed() -> None:
    """注入脚本已删除。/ Injection script removed."""
    assert not (ROOT / "scripts/prepare_telemetry_build.py").exists()


def test_telemetry_module_has_no_countly_reference() -> None:
    """遥测模块不再提及 Countly。/ No Countly reference left."""
    source = (ROOT / "app_user_stat/telemetry.py").read_text(encoding="utf-8")
    assert "countly" not in source.lower()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_no_countly_residue.py -v`
Expected: 三个用例全 FAIL

- [ ] **Step 3: 删除注入脚本与 CI 步骤**

```bash
git rm scripts/prepare_telemetry_build.py
```

在 `.github/workflows/build-release.yml` 中删除 Windows（约 L44-48）与 macOS（约 L195-199）两处 `Prepare telemetry build` 步骤及其 `env` 块。**其余步骤不动。**

- [ ] **Step 4: 重写 README_TELEMETRY.md**

`app_user_stat/README_TELEMETRY.md` 全文替换为：

```markdown
# SuperPicky 遥测说明 / Telemetry Notes

实现位于 `app_user_stat/telemetry.py`，设计见
`docs/specs/2026-08-21-usage-analytics-design.md`。

## 采集什么 / What is collected

每次启动最多三个事件（`install` / `app_start` / `heartbeat_weekly`，后者 7 天一次），
每个事件附带：应用版本、操作系统、CPU 架构、Python 版本、界面语言，以及一个
**按日轮换**的匿名 ID（`sha256(本地安装ID + UTC日期)`）。

本地安装 ID 存于 `telemetry_state.json`，**永不上报**。上报 ID 跨日即变，
因此不构成持久标识符——这是「默认开启」得以成立的前提，代价是算不了留存。

**绝不采集**：用户名、邮箱、MAC 地址、硬件序列号、照片内容、文件路径、原始 EXIF。

## 开关 / Toggle

由 `advanced_config` 的 `telemetry_enabled` 控制（默认 `True`），
界面在设置中心「关于」页。**不要引入独立 json 存放这个开关**——
设置的唯一存储是 `advanced_config`（见 `CLAUDE.md`）。

## 端点 / Endpoint

`POST https://superpicky.app/t`，由官网 Worker 接收并写入 Cloudflare
Analytics Engine。**该端点不做身份认证**：开源客户端藏不住密钥，任何人
都能伪造数据点。因此这套数字的定位是趋势观察，而非可审计的精确指标。

请求契约与字段白名单见 `SuperPicky-Site` 仓库的 `src/telemetry.js`；
改动任一端都必须同步另一端，`test_telemetry_send.py` 守住 app 侧契约。

## 历史 / History

4.x 曾使用 Countly Flex（PR #78）。该实例的域名已不存在，导致数月间所有
打包版向失效地址静默投递、零数据落地且无人察觉。2026-08 改为上述自建方案。
**已发布的旧版本硬编码了失效域名，其数据永久缺失，无法补救。**

## 验证 / Verification

```bash
python3 -m app_user_stat.telemetry           # 自检，不发送
python3 -m app_user_stat.telemetry --send    # 自检并实际发送
```
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest test_no_countly_residue.py -v`
Expected: PASS（3 个用例）

- [ ] **Step 6: 全量回归**

Run: `python3 -m pytest -q`
Expected: 全绿。基线为 417 个测试全绿（2026-08-11 审计结论），新增本计划的用例后数量应上升。若出现红灯，先确认不是「测试写了真实 advanced_config」这类环境污染。

- [ ] **Step 7: 在 GitHub 删除失效 secret**

```bash
gh secret delete COUNTLY_APP_KEY -R jamesphotography/SuperPicky
gh secret delete COUNTLY_SERVER_URL -R jamesphotography/SuperPicky
```

- [ ] **Step 8: 提交**

```bash
git add -A .github/workflows/build-release.yml app_user_stat/README_TELEMETRY.md test_no_countly_residue.py
git commit -m "chore(telemetry): 清除 Countly 残留

删注入脚本与两处 CI 步骤、重写说明文档并记录这段历史——
留着失效的注入步骤正是「配置齐全但数据为零」误判的来源。"
```

---

## 收尾验收 / Final Acceptance

两个仓库都完成后：

- [ ] `SuperPicky-Site`：`npx vitest run` 全绿；`npx wrangler deploy` 成功
- [ ] 线上 `https://superpicky.app/` 返回 200，`/no-such-page` 返回 **404**，`/dl/mac_arm64-v4.5.0-baidu` 返回 302
- [ ] `SuperPicky2026`：`python3 -m pytest -q` 全绿
- [ ] 本机运行 `python3 -m app_user_stat.telemetry --send`，随后 `curl "https://superpicky.app/stats?token=..."` 能在 `versionMix` 中看到这一条
- [ ] 设置中心关掉开关后重启应用，确认无任何请求发往 `/t`（可用 `wrangler tail` 观察）

**上线后的预期**：曲线在最初几周反映的是**新版本渗透率**，不是真实用户增长——存量用户要等升级到含新遥测的版本后才会出现。不要把这段爬坡误读为增长。
