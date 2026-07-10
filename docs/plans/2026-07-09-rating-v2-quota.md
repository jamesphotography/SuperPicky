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
- [x] T2 TOPIQ 改打鸟裁剪区(bird_crop_bgr,无裁剪回退整图;
      无鸟详情路径 calculate_rejected_quality_detail 保持整图不变)
- [x] T3 photo_processor 两遍定星改造:
      - [x] T3a 识鸟提交门控改「星级≥2 或 硬门槛+锐度≥250」
      - [x] T3b 两遍定星:advanced_config 加 rating_algorithm(默认v2)/custom_quota3;
        循环内 gate_photo 判池,池内照片的星级 EXIF(queue_star_metadata 挂起)、
        统计、star_3_photos、file_ratings 全部延后;收尾 assign_ratings 统一定星
        →回填 EXIF(重写 caption 首行)/统计/DB rating→入队终批;i18n rating_v2.* 键
      - 已知留痕:循环中单张日志仍显示 v1 预估星级(收尾打 V2 汇总行);
        star2_reasons 仍按 v1 口径(2★子目录分类,待 T5/T6 时一并处理)
- [x] T4 配额 UI:设置中心精选页与首页快速面板均改「3星配额」单滑块(5-50,
      与 set_custom_quota3 clamp 一致),v2 下隐藏旧阈值滑块(仍构建,供 v1 回滚
      与既有测试);技能卡片联动配额;手动改配额→custom 档;i18n 中英已加。
      顺带修复:硬门槛置信度改跟随用户 AI 置信度设置(gate_photo min_confidence
      参数化,photo_processor 判池与终评均传 settings.ai_confidence)
- [x] T5 清偿口径不一致:adj_sharpness/adj_sharpness_csv 统一含 ISO 归一化
      (评星实际输入口径);删 V3.8 加法加成残留 rating_sharpness/rating_topiq
      与 star2_reasons(全仓库无读取方,死状态整块移除)
- [x] T6 rating_mover 核查通过:与评分引擎零耦合(纯文件移动),无需改动
- [x] 方案A(用户拍板):配额分母=排序池;滑块标签写明「可选照片中前N%,
      无鸟/糊片不占分母」,汇总日志加「占全部 {overall}%」
- [x] T8 按鸟种配额(用户提议):识鸟开启时配额按鸟种分组执行——排序仍用
      全局 Q(种内百分位小样本噪声大),每种 3★=ceil(种内张数×配额%),
      小样本鸟种保底最好 1 张(锐度兜底/眼睛封顶仍生效);未识别归一组;
      识鸟关闭时全 None 单组=全局配额,无需开关。汇总日志追加按鸟种
      3星/池内 分布行
- [x] T9 处理中显示去星级化(用户提议):池内照片星级待定,滚动日志显示
      「⏳ 锐度·美学」指标行(硬门槛终局照片照旧),右侧预览 rating 传 None
- [x] T10 评星算法选择 UI(用户提议):设置中心精选页两张卡(V2 批内配额=默认推荐/
      V1 绝对阈值=旧版),点卡即落盘+两处滑块可见性实时切换;
      spec: docs/specs/2026-07-10-rating-algo-selector-design.md
- [ ] T7 验证:单测全绿;同一目录 nightly vs dev 全流程对比(住处鸟片1 + Test-Superpicky);
      py_compile;中文 EXIF 写读回

## 风险与回滚

- V2 定星依赖全批统计 → 续跑(resume)时批内百分位基于本次续跑的子集;可接受
  (文档说明),或后续用 DB 里历史指标合并计算。
- 若现网反馈不佳,rating_engine 旧路径保留(config 开关 rating_algorithm=v1/v2),
  默认 v2,回滚只需切开关。
