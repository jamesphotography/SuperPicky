# 评星 V2:批内相对排序 + 配额制 —— 实施计划

日期:2026-07-09 · 分支:dev · 状态:进行中
背景与审计证据见 memory(rating-v2-relative-quota)与 tools/rating_v2_prototype.py。

## 已审定决策(James 拍板)

- 星级语义 = **批内相对**(同一张照片不同批次星级可不同,与人类选片一致)。
- 硬门槛保留绝对值:无鸟→-1;置信度<50%→0;ISO归一化锐度<100→0;TOPIQ<3.5→0;关键点全不可见→1。
- Q = 0.65×锐度批内百分位 + 0.35×TOPIQ批内百分位;飞鸟+0.06、精焦(BEST)+0.04、脱焦(WORST)−0.06、曝光问题−0.06;眼睛可见度<0.5 → 星级封顶 2★。
- 配额:**3★ = Q 前 20%**(叠加绝对兜底:归一化锐度≥300),2★ = 20%–45%,其余 1★。
- **TOPIQ 改打鸟裁剪区**(bird_crop_bgr,两目录实测:整图分在小鸟场景=背景噪声,r=0.24;纯裁剪,不混合)。
- 技能等级 → 配额映射:新手 25% / 进阶 20% / 大师 10%。
- 连拍组内 3★ 封顶 2 张(超出者降 2★)。
- 识鸟提交门控:从「星级≥2」改为「过硬门槛 + 归一化锐度≥250 粗筛」。

## 任务分解

- [x] T0 原型验证(tools/rating_v2_prototype.py,两目录实测通过)
- [x] T1 核心模块 `core/rating_quota.py`:纯函数批量定星(硬门槛+Q+配额+封顶+连拍cap),
      无 Qt/IO 依赖,含单测 test_rating_quota.py
- [ ] T2 TOPIQ 改打鸟裁剪区(photo_processor Phase 3:orig_img→bird_crop_bgr;
      整图分保留给无鸟详情路径 calculate_rejected_quality_detail 不变)
- [ ] T3 photo_processor 两遍定星改造(最大项):
      - 循环内:照常算指标+硬门槛,星级列 pending;星级相关 EXIF(rating/pick/caption)
        不再逐张 queue,收集到 pending 列表
      - 后置阶段(collect_birdid 之后、picked 旗标之前):调 rating_quota 统一定星
        → 更新 file_ratings/DB/日志 → 批量 queue EXIF → flush
      - 处理中日志:每张打指标(锐度/TOPIQ/飞鸟),星级在收尾统一打
      - 识鸟提交门控改硬门槛+锐度≥250(photo_processor:2468,:2503)
- [ ] T4 skill_presets:阈值→配额映射(新手25/进阶20/大师10 + custom_quota3);
      settings_center 精选页与首页快速面板滑块改配额语义(SSOT 约定:setter clamp=UI范围);
      i18n 中英文案
- [ ] T5 清偿口径不一致:DB adj_* = 评星实际输入(含ISO归一化);删 V3.8 加法加成残留
      (photo_processor:2178-2183,:2625 的 rating_sharpness/rating_topiq)
- [ ] T6 结果浏览器改星联动:改星移动文件逻辑不变;重评星入口若用旧引擎需对齐(核查 rating_mover)
- [ ] T7 验证:单测全绿;同一目录 nightly vs dev 全流程对比(住处鸟片1 + Test-Superpicky);
      py_compile;中文 EXIF 写读回

## 风险与回滚

- V2 定星依赖全批统计 → 续跑(resume)时批内百分位基于本次续跑的子集;可接受
  (文档说明),或后续用 DB 里历史指标合并计算。
- 若现网反馈不佳,rating_engine 旧路径保留(config 开关 rating_algorithm=v1/v2),
  默认 v2,回滚只需切开关。
