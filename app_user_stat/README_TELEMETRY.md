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

## 如何发现它又死了 / How you'll know if it dies again

上一节那件事之所以能持续数月，不是因为没有日志，而是因为**没有人会去看**：
异常按设计全部吞掉（这是对的，统计绝不能拖垮启动），于是「管道断了」与
「没人用」在本地表现得一模一样——都是安静。**下面这一节是这份文档存在的
真正理由。**

看板地址（token 见 Cloudflare Worker 的 `STATS_TOKEN` secret，勿写进仓库）：

```
https://superpicky.app/stats?token=<STATS_TOKEN>
```

怎么读：

| 现象 | 结论 |
| --- | --- |
| `dailyActive` 有逐日数据，数量级与预期相符 | 管道通 |
| **发版之后 `dailyActive` 仍为 0 或整个数组为空** | **是管道断了，不是没人用**——正式版发出去必然有人启动 |
| `dailyActive` 正常但 `pageviews` 为 0 | Worker 在跑但 `run_worker_first` 或 `logPageview` 被改坏了 |
| 某个键是 `{"error": "HTTP 401/403"}` | `CF_API_TOKEN` / `CF_ACCOUNT_ID` 失效或权限不足 |
| `github` 是 `{"error": "HTTP 403"}` | GitHub 未认证请求的每小时 60 次限流，等一会儿再看 |
| 整个 `/stats` 返回 401 | `STATS_TOKEN` 没配或 token 传错 |
| `dailyActive` 无故下滑，且没有发版或公告能解释 | 查 Cloudflare 控制台 observability 下 `/t` 的 429 速率（限流已在 `wrangler.jsonc` 打开）——多半是限流器在共享出口 IP（校园网/机房 CGNAT）上误伤了合法用户，不是真的有人流失 |

固定节奏：**每次发版后查一次**，把它写进发版清单
（`docs/release-checklist.md` §4.1 最后一条就是这件事）。别的时候不查也行，
但发版后这一次不能省——那正是唯一能把「管道断了」和「没人用」区分开的时刻，
因为此刻你确知有人在启动新版本。

顺带一提，客户端侧「投递失败」永远只在 `TELEMETRY_DEBUG=1` 时打一行日志
（见 `_debug_log`），普通用户机器上不会有任何痕迹。**别指望用户报告它。**

Because every error is swallowed by design, a dead pipeline and zero users
look identical from the app side. The `/stats` dashboard above is the only
place the difference shows up, and the moment it shows up is right after a
release — that is when you know for certain that people are launching the
app, so a `dailyActive` of zero means the pipeline, not the audience. Check
it after every release.

## 验证 / Verification

```bash
python3 -m app_user_stat.telemetry           # 自检，不发送
python3 -m app_user_stat.telemetry --send    # 自检并实际发送
```

这两条只证明**本机到端点这一段**是通的（能发出去、拿到 2xx），
证明不了数据真的落进了 Analytics Engine——那一段只有上面的 `/stats` 能看到。

These prove only that this machine can reach the endpoint, not that the data
landed in Analytics Engine. Only `/stats` shows that.
