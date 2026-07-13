# 检测与整理解耦：平铺模式 + 连拍子目录开关 设计
# Detection/Organization Decoupling: Flat Layout + Burst Subfolder Toggle

日期：2026-07-13 ｜ 分支：dev ｜ 状态：已获用户批准（Paul 反馈 P1-2/P1-3）

## 背景 / Background

外部用户 Paul：①希望处理后主文件夹保持平铺（flat），不重组目录——移动文件
会破坏 Lightroom 已导入目录的引用，是 LR 编目用户的采用障碍；②希望保留
连拍检测但不要分出大量 burst 子文件夹。

已核实的现状（本 spec 的地基）：
- `photo_processor.process_directory(organize_files: bool)` 已存在且被
  CLI `--organize` 长期使用；`organize_files=False` 时不移动文件、不建
  burst 目录、不写 manifest。GUI 在 `ui/main_window.py:519/607` 写死 True。
- 结果浏览器完全依赖 report.db（含 `burst_id/burst_position` 列），
  不依赖目录结构；星级/鸟种/连拍信息在平铺下全部可用。
- `core/rating_mover.py` 改星(:102)与改鸟种(:259)对根目录照片天然跳过
  文件移动（EXIF/DB 照写）——平铺下浏览器编辑安全。

## 已审定决策 / Approved Decisions

- 连拍组号**不写 EXIF**，只留 DB（浏览器可见，LR 无感）。
- 普通布局下加**独立开关**「连拍归入独立子文件夹」（默认开=现状）。

## A. 平铺模式 / Flat Layout

1. **布局层**（`core/folder_layout.py`）：新增 `LAYOUT_FLAT = "flat"`，
   加入 `VALID_LAYOUTS`；`compute_target_folder(..., layout="flat")`
   返回 `""`（防御性——正常流程 organize gate 下不会被调用）。
   `normalize_layout` 自然接受 "flat"。
2. **GUI 接线**（`ui/main_window.py` :519/:607 两处）：
   `organize_files=(cfg.folder_layout != LAYOUT_FLAT)`。
3. **设置 UI**（设置中心输出页 `folder_layout` 下拉 :1200）：加第三项
   「平铺——识别评分但不移动文件（Lightroom 友好）/ Flat — rate in
   place, no file moves (Lightroom-friendly)」，i18n 键
   `advanced_settings.folder_layout_flat` 中英。
4. **平铺语义**：所有照片（含无鸟/0星）留原地；EXIF 星级/关键字/精选旗标
   照写；DB `current_path` 保持根目录相对路径；不写
   `.superpicky_manifest.json`（reset 现有语义容忍：只清 EXIF/DB）。
5. **主界面视频自动归类**：平铺模式下**整体跳过**视频自动处理并写日志
   `logs.video_skip_flat`（实施时修正：组织器 OrganizeOptions 仅有
   move/copy 无 no-op 模式，且分析结果除归类改名外无落地产物，"只分析
   不归类"没有产出，故整体跳过；接入点 `main_window._process_videos`
   的 flat gate）。
6. **边界回归钉**（无代码改动，测试锁定既有行为）：rating_mover 根目录
   照片跳过移动；浏览器筛选不依赖目录。

## B. 连拍子目录开关 / Burst Subfolder Toggle

1. **配置**：`advanced_config` 加 `burst_group_folders`（默认 `True`）+
   property/`set_burst_group_folders`（setter 内部 save，跟随 birdid_*
   惯例亦可不 save——以实现时该文件相邻 setter 惯例为准，二者择一并在
   UI 保存处补 save）。
2. **处理器 gate**（`core/photo_processor.py:558`）：
   `if self.settings.detect_burst and self.burst_map and organize_files
   and self.config.burst_group_folders:`——关闭后连拍照片按各自星级/
   鸟种走常规归档；连拍检测、DB burst 列、评分阶段连拍 3★ 封顶
   （rating_quota burst_cap3）全部不受影响。
3. **设置 UI**：设置中心精选页「检测开关」区连拍检测旁加复选框
   「连拍归入独立子文件夹 / Group bursts into subfolders」，i18n 键
   `settings.culling_burst_folders_label` 中英。
4. **与平铺关系**：平铺模式下整体不移动文件，此开关无效果；UI 不做
   联动置灰（保持简单，文档说明即可）。

## 测试 / Testing

- `compute_target_folder` flat 分支返回 `""`；`normalize_layout("flat")`
  合法。
- main_window 两处 organize_files 传值随 folder_layout 切换（源检查或
  offscreen 构造断言调用参数——以可行者为准）。
- burst gate 单测：`burst_group_folders=False` 时 `_consolidate_burst_groups`
  不被调用（或其 gate 条件为假）。
- 设置 UI：输出页第三项存在且保存往返；精选页 burst 复选框保存往返。
- rating_mover 根目录跳过：回归钉（构造根目录照片记录调
  `move_photo_on_metadata_change`，断言文件未移动）。

## 不做 / Out of Scope

- burst 组号写 EXIF（用户拍板）。
- 视频归类流程自身重构（仅在平铺下跳过移动）。
- 旧已整理目录的"迁回平铺"迁移工具。
- 平铺模式下 burst 开关的 UI 联动置灰。
