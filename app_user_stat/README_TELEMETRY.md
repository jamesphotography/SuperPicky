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
