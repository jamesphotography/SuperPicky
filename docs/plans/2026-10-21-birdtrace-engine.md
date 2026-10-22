# BirdTrace 识鸟模型接入实施计划 / BirdTrace Engine Implementation Plan

设计文档 / Spec：`docs/specs/2026-10-21-birdtrace-engine-design.md`
分支 / Branch：`feat/birdtrace`（worktree：`../SuperPicky2026-birdtrace`）

每个任务完成后：`python3 -m py_compile` 改动文件 + 相关测试通过 + 单独提交。

## T1 通用类别映射模块
- 新建 `birdid/class_mapping.py`：`NUM_CLASSES`、`norm_name()`、`build_class_map()`（分类对照 → eBird 代码 → 学名 →
  英文名兜底）、`aggregate_probs()`。
- 测试 `tests/test_class_mapping.py`：撇号归一化、优先级、多对一相加、越界与未命中记 -1。

## T2 对照表生成脚本与对照表
- `scripts_dev/build_birdtrace_class_map.py`：输入 BirdTrace `classes.json` + eBird 公开分类表 CSV
  （`https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv`，开发时下载，不入库）+ 鸟种库；
  输出 `birdid/data/birdtrace_class_map.json`（含来源、eBird 分类表行数、生成时间、对上数量）。
- 生成并提交 JSON；报告对上数量、AU-VIC 覆盖。

## T3 BirdTrace 推理模块
- `birdid/birdtrace_classifier.py`：PyTorch（timm fastvit_ma36 + checkpoint_filter_fn + reparameterize），CPU 先加载
  再搬 `get_best_device()`，失败退 CPU；`logits()` / `probs()`；`birdtrace_available()`；懒加载单例。
- 模型目录：`models/birdtrace/`（`model.safetensors`）；对照表在 `birdid/data/`。
- 测试：可用性判断、对照表维度校验；有模型文件时跑一张图的冒烟（无模型时 skip）。

## T4 识鸟模型选项与对比模式
- `bird_identifier.py`：`BIRDID_ENGINES = ("compare", "birdtrace", "osea")`、`get_birdid_engine()`（环境变量优先，
  无效值→compare）、`primary_engine()`、`check_engine()`、`_class_probs` 分派、BirdTrace 不可用时退回 OSEA；
  `identify_bird` 返回 `engine/primary_engine/compare`；对比 CSV。
- `advanced_config.py`：`birdid_engine` 默认 `compare` + property/setter（取值与下拉框一致）。
- 测试 `tests/test_birdid_engine.py`。

## T5 题注、报告库、选片流程
- `core/photo_processor.py`：记录主模型名与验证结果；`core/rating_caption.py` 题注写验证模型第一名；
  `tools/report_db.py` 新增 verify_* 列（老库迁移）+ 线程安全写入。
- 测试：题注、报告库迁移与多线程写读。

## T6 结果浏览器「只看分歧」
- `tools/report_db.py` 筛选条件 + `ui/filter_panel.py` 复选框（没有验证数据时隐藏）。
- 测试：分歧判定随当前鸟种（含待确定候选）即时更新。

## T7 设置中心
- 识鸟页「识鸟模型」下拉框（对比 / BirdTrace / OSEA），说明文字含对比 CSV 路径；BirdTrace 模型缺失时提示。
- 关于页署名：BirdTrace · Warden · HF 链接 · CC BY-NC 4.0；FastViT（Apple）。三语。
- 测试：下拉框持久化、缺模型时提示。

## T8 模型分发与打包
- 上传到 `jamesphotography/SuperPicky-models`（需用户 HF 写权限令牌，用户执行或确认后执行）。
- `scripts/download_models.py` 登记 + SHA256；两个 `.spec` 补 `timm.models.fastvit`、`safetensors`、
  `birdid/data/birdtrace_class_map.json`、`THIRD_PARTY_NOTICES.txt`。

## T9 验证
- 全量 pytest；五批数据准确率回归；端到端测速（OSEA / BirdTrace / 对比）；CPU 模式测速；macOS 打包冒烟。
