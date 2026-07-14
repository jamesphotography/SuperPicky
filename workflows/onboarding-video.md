# 半自动录制 onboarding 视频

目标：把 `docs/tutorial-4.2.1.html` 里的稳定教程结构，转成一个可重复更新的录制流程，而不是每次手工重新写分镜。

## 产物

- 结构化教程数据：`docs/Promotion/onboarding/tutorial-4.2.1-structure.json`
- 全量 shot list：`docs/Promotion/onboarding/tutorial-4.2.1-shotlist.md`
- 精简 onboarding 录制单：`docs/Promotion/onboarding/tutorial-4.2.1-onboarding.md`
- 精简 onboarding CSV：`docs/Promotion/onboarding/tutorial-4.2.1-onboarding.csv`

这些文件由 `scripts/generate_onboarding_assets.py` 生成。

## 生成命令

```bash
python3 scripts/generate_onboarding_assets.py
```

如果后续教程页版本变化，可指定不同输入和输出目录：

```bash
python3 scripts/generate_onboarding_assets.py \
  --source docs/tutorial-4.2.1.html \
  --out-dir docs/Promotion/onboarding
```

## 推荐录制流程

1. 准备一个小型样片目录，避免录制时等待太久。
2. 把应用窗口固定为稳定尺寸，录制时不要频繁拖动窗口。
3. 打开 `tutorial-4.2.1-onboarding.md`，按 `S01` 到 `S10` 顺序逐段录制。
4. 每段只完成一个明确动作，比如“选目录”“切换功能开关”“打开结果浏览器”。
5. 某一段失误时，只重录该段对应的 `filename`，不要从头重来。
6. 剪辑时按 `tutorial-4.2.1-onboarding.csv` 的顺序拼接，再统一加字幕或旁白。

## 为什么这是“半自动”

- 自动部分：从教程页提取章节、步骤标题、核心导语，并生成录制清单。
- 手动部分：真正的 UI 操作、镜头节奏、鼠标轨迹和最终剪辑仍然由人控制。

这种拆分比较稳，因为教程文字在变，录制逻辑也能跟着重新生成；但具体画面表现仍然保留人为判断，不会变成机械演示。
