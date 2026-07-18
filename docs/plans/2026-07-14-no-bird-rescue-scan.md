# 无鸟补救扫描（两段式检测）实施计划 / No-Bird Rescue Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 YOLO 快速路径即将拒绝（无鸟/低置信度）时，用 1024px 低阈值重扫 + BirdID 分类器守门，把漏检的真鸟救回来。

**Architecture:** 补救逻辑全部内聚在 `ai_model.detect_and_draw_birds`（GUI/CLI 唯一检测入口）：第一遍 640 检测低于 UI 阈值时触发 `_rescue_scan()`，救回候选覆盖第一遍解析结果后复用原有裁剪/入库流程；返回元组扩为 10 元（追加 `rescued`），`photo_processor` 对救回照片豁免下游置信度门槛。设置走 `advanced_config` SSOT。

**Tech Stack:** Python 3 (.venv)、ultralytics YOLO11l-seg、OSEA BirdID 分类器（经 `core.birdid_adapter`）、PySide6 设置中心、pytest。

**Spec:** `docs/specs/2026-07-14-no-bird-rescue-scan-design.md`

## Global Constraints

- 一切用 `.venv/bin/python`（含 pytest / py_compile），不用系统 Python。
- UTF-8 安全：所有文件读写 `encoding='utf-8'`；不用 sed/awk 改含中文文件。
- 注释规范：中文 + 英文成对；docstring 中英双语。
- 跨平台（Windows + macOS）：不新增平台相关代码路径。
- SSOT：设置只进 `advanced_config`（DEFAULT_CONFIG + property/setter），setter clamp 必须与 UI 控件范围一致；禁止新 json 或控件本地状态。
- 每个 Task 结束跑 `.venv/bin/python -m py_compile <改动文件>` + 相关 pytest，然后 commit。
- 提交信息结尾：`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: advanced_config 新增 rescue 设置字段

**Files:**
- Modify: `advanced_config.py`（DEFAULT_CONFIG ~L90 区域；setter 区 ~L305 附近）
- Test: `test_advanced_config_rescue.py`（新建，仿 `test_correction_consent_config.py` 的临时文件隔离模式）

**Interfaces:**
- Produces: `cfg.rescue_scan_enabled -> bool`（默认 True）、`cfg.set_rescue_scan_enabled(value)`；`cfg.rescue_birdid_gate -> int`（默认 10）、`cfg.set_rescue_birdid_gate(value)`（clamp 0~100）。Task 4/6 依赖这些名字。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
advanced_config.py 无鸟补救扫描字段单测。

⚠️ 隔离要点：AdvancedConfig() 默认读写用户真实 advanced_config.json，
测试必须指向临时文件，避免污染用户配置（同 test_correction_consent_config.py）。

Rescue-scan config tests. Must use an isolated temp config file so tests
never mutate the user's real advanced_config.json.
"""
import os
import tempfile

from advanced_config import AdvancedConfig


def _isolated_config() -> AdvancedConfig:
    """构造指向临时文件的隔离配置实例 / isolated instance backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix="_advanced_config.json")
    os.close(fd)
    os.remove(path)
    return AdvancedConfig(config_file=path)


def test_rescue_defaults():
    cfg = _isolated_config()
    assert cfg.rescue_scan_enabled is True
    assert cfg.rescue_birdid_gate == 10


def test_rescue_setters_and_clamp():
    cfg = _isolated_config()
    cfg.set_rescue_scan_enabled(False)
    assert cfg.rescue_scan_enabled is False
    cfg.set_rescue_birdid_gate(150)
    assert cfg.rescue_birdid_gate == 100
    cfg.set_rescue_birdid_gate(-5)
    assert cfg.rescue_birdid_gate == 0
    cfg.set_rescue_birdid_gate(25.7)
    assert cfg.rescue_birdid_gate == 25
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_advanced_config_rescue.py -v`
Expected: FAIL（`AttributeError: ... rescue_scan_enabled`）

- [ ] **Step 3: 实现**

`DEFAULT_CONFIG` 里（`burst_group_folders` 行之后）追加：

```python
        # V4.6: 无鸟补救扫描 (spec: docs/specs/2026-07-14-no-bird-rescue-scan-design.md)
        # V4.6: No-bird rescue scan
        "rescue_scan_enabled": True,   # 判无鸟/低置信度时触发 1024px 重扫 + 识鸟守门
        "rescue_birdid_gate": 10,      # 弱候选的识鸟确认门槛 (0-100, top1 置信度百分比)
```

setter 区（`set_min_confidence` 附近）追加，property 加在 getter 区：

```python
    @property
    def rescue_scan_enabled(self):
        return self.config.get("rescue_scan_enabled", True)

    @property
    def rescue_birdid_gate(self):
        return self.config.get("rescue_birdid_gate", 10)

    def set_rescue_scan_enabled(self, value):
        """设置无鸟补救扫描开关 / Toggle the no-bird rescue scan."""
        self.config["rescue_scan_enabled"] = bool(value)

    def set_rescue_birdid_gate(self, value):
        """设置补救识鸟确认门槛 (0-100) / Rescue BirdID gate percent (0-100)."""
        self.config["rescue_birdid_gate"] = max(0, min(100, int(value)))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest test_advanced_config_rescue.py -v`
Expected: 2 passed

- [ ] **Step 5: py_compile + commit**

```bash
.venv/bin/python -m py_compile advanced_config.py test_advanced_config_rescue.py
git add advanced_config.py test_advanced_config_rescue.py
git commit -m "feat(config): 无鸟补救扫描设置字段 rescue_scan_enabled/rescue_birdid_gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: BirdID 分类器模块级推理锁

**背景 / Why:** 批处理时 BirdID executor（MPS/CUDA 单 worker）与新增的补救确认会从不同线程并发调用分类器 forward。现状仅靠 executor 自身单 worker 约束，无跨调用方保护。加模块级锁把所有分类器 forward 串行化。

**Files:**
- Modify: `birdid/bird_identifier.py`（`predict_bird` L853-871；`threading` 已在 L50 导入）
- Test: `test_birdid_classifier_lock.py`（新建）

**Interfaces:**
- Produces: `birdid.bird_identifier._CLASSIFIER_INFER_LOCK`（threading.Lock，Task 7 验证引用）。`predict_bird` 对外签名不变。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
分类器推理锁存在性单测（轻量：不加载模型）。

Classifier inference-lock presence test (lightweight: no model load).
"""
import threading


def test_classifier_infer_lock_exists():
    import birdid.bird_identifier as bi
    assert isinstance(bi._CLASSIFIER_INFER_LOCK, type(threading.Lock()))
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_birdid_classifier_lock.py -v`
Expected: FAIL（AttributeError）

注意：若 import `birdid.bird_identifier` 本身在无模型环境报错，参考 `test_bird_identifier_gps_zero_coords.py` 的既有导入方式对齐处理。

- [ ] **Step 3: 实现**

`birdid/bird_identifier.py` 模块级（`threading` import 之后的顶层区域）加：

```python
# 分类器推理锁：批处理 BirdID executor 与补救扫描确认可能跨线程并发调用
# forward，MPS/CUDA 下并发安全性有限，统一串行化。
# Classifier inference lock: the batch BirdID executor and the rescue-scan
# confirmation may call forward concurrently from different threads; MPS/CUDA
# concurrency safety is limited, so all forwards are serialized here.
_CLASSIFIER_INFER_LOCK = threading.Lock()
```

`predict_bird` 内把张量上载 + forward 包进锁（L868-871 现状）：

```python
    input_tensor = transformed_tensor.unsqueeze(0)

    with _CLASSIFIER_INFER_LOCK:
        input_tensor = input_tensor.to(CLASSIFIER_DEVICE)
        with torch.no_grad():
            output = model(input_tensor)[0]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest test_birdid_classifier_lock.py -v`
Expected: PASS

- [ ] **Step 5: py_compile + commit**

```bash
.venv/bin/python -m py_compile birdid/bird_identifier.py test_birdid_classifier_lock.py
git add birdid/bird_identifier.py test_birdid_classifier_lock.py
git commit -m "fix(birdid): 分类器 forward 加模块级推理锁,保护跨线程并发调用

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 补救扫描核心函数 `_rescue_scan`（纯逻辑 + FakeModel 单测）

**Files:**
- Modify: `config.py`（`AIConfig` L361-363 附近加常量）
- Modify: `ai_model.py`（在 `_get_iqa_scorer` 之后、`detect_and_draw_birds` 之前加 `_get_rescue_birdid` / `_birdid_confirm` / `_rescue_scan`）
- Test: `test_ai_model_rescue_scan.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `rescue_birdid_gate` 语义（百分比 0-100）。
- Produces（Task 4 依赖，签名逐字）:
  - `config.ai.RESCUE_IMGSZ: int = 1024`、`config.ai.RESCUE_CONF: float = 0.05`、`config.ai.RESCUE_CONFUSABLE_CLASS_IDS: dict = {4: "airplane", 33: "kite"}`
  - `_rescue_scan(model, image, accept_conf: float, birdid_gate: int, dir, i18n) -> Optional[dict]`，成功返回 `{"xyxy": np.ndarray(4,), "conf": float, "mask": Optional[np.ndarray], "source": str, "species": str, "species_conf": float}`；失败/无候选返回 `None`。
  - `_birdid_confirm(image, xyxy) -> tuple[str, float]`（鸟种名, 置信度百分比 0-100；失败返回 `("", 0.0)`）——模块级函数，便于测试 monkeypatch。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
ai_model._rescue_scan 单测：用 FakeModel 模拟 ultralytics 结果，
不加载真实 YOLO/BirdID 模型。

覆盖 4 条路径：直接接受 / 弱候选识鸟通过 / 弱候选识鸟拒绝 / 无候选。

_rescue_scan unit tests with a FakeModel mimicking ultralytics results;
no real YOLO/BirdID model is loaded. Covers direct-accept, gate-accept,
gate-reject and no-candidate paths.
"""
import numpy as np
import torch

import ai_model


class FakeBoxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = torch.tensor(xyxy, dtype=torch.float32)
        self.conf = torch.tensor(conf, dtype=torch.float32)
        self.cls = torch.tensor(cls, dtype=torch.float32)

    def __len__(self):
        return len(self.conf)


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes
        self.masks = None


class FakeModel:
    """返回预设检测结果的假 YOLO / Fake YOLO returning canned detections."""

    def __init__(self, xyxy, conf, cls):
        self._r = FakeResult(FakeBoxes(xyxy, conf, cls))

    def __call__(self, image, **kwargs):
        return [self._r]


IMG = np.zeros((683, 1024, 3), dtype=np.uint8)


def test_direct_accept_bird_above_threshold():
    model = FakeModel([[10, 10, 60, 60]], [0.8], [14])
    r = ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None)
    assert r is not None and r["source"] == "bird"
    assert abs(r["conf"] - 0.8) < 1e-6


def test_weak_bird_gate_accept(monkeypatch):
    model = FakeModel([[10, 10, 60, 60]], [0.12], [14])
    monkeypatch.setattr(ai_model, "_birdid_confirm",
                        lambda image, xyxy: ("红脚鹬", 81.7))
    r = ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None)
    assert r is not None and r["species"] == "红脚鹬"


def test_kite_candidate_gate_reject(monkeypatch):
    model = FakeModel([[10, 10, 60, 60]], [0.85], [33])  # kite
    monkeypatch.setattr(ai_model, "_birdid_confirm",
                        lambda image, xyxy: ("某鸟", 4.0))
    assert ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None) is None


def test_no_candidate_returns_none():
    model = FakeModel([[10, 10, 60, 60]], [0.9], [0])  # person
    assert ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_ai_model_rescue_scan.py -v`
Expected: FAIL（`AttributeError: module 'ai_model' has no attribute '_rescue_scan'`）

- [ ] **Step 3: 实现常量（config.py）**

`AIConfig`（`TARGET_IMAGE_SIZE: int = 1024` 之后）：

```python
    # V4.6: 无鸟补救扫描参数 / No-bird rescue scan parameters
    RESCUE_IMGSZ: int = 1024      # 补救重扫推理分辨率 / rescue rescan imgsz
    RESCUE_CONF: float = 0.05     # 补救重扫置信度地板 / rescue conf floor
    # COCO 中飞鸟常被误认的类别 / COCO classes birds in flight are mistaken for
    RESCUE_CONFUSABLE_CLASS_IDS: dict = field(
        default_factory=lambda: {4: "airplane", 33: "kite"})
```

注意：`AIConfig` 若是 `@dataclass`，dict 字段必须用 `field(default_factory=...)`（检查文件头部是否已 `from dataclasses import field`，没有则补）。若 `AIConfig` 不是 dataclass 而是普通类属性，直接 `RESCUE_CONFUSABLE_CLASS_IDS = {4: "airplane", 33: "kite"}`。

- [ ] **Step 4: 实现 `_rescue_scan`（ai_model.py）**

```python
def _get_rescue_birdid():
    """
    获取补救确认用的 BirdID 适配器单例（懒加载，经 lazy registry 管理）。

    返回:
    BirdIDAdapter: 适配器实例（底层模型与批处理识鸟共享，无重复显存）

    Lazily get the BirdID adapter singleton for rescue confirmation via the
    lazy registry; the underlying model is shared with batch bird-ID.
    """
    registry = get_lazy_registry()

    def _factory():
        from core.birdid_adapter import BirdIDAdapter
        return BirdIDAdapter()

    return registry.get_or_create("ai_model.rescue_birdid_adapter", _factory)


def _birdid_confirm(image: np.ndarray, xyxy) -> tuple:
    """
    把候选框裁下来交给 BirdID 分类器确认是否为鸟。

    参数:
    image (np.ndarray): BGR 整图（长边 1024 预处理后）
    xyxy: 候选框 (x1, y1, x2, y2)

    返回:
    tuple[str, float]: (top1 鸟种名, 置信度百分比 0-100)；失败返回 ("", 0.0)

    Crop the candidate box and ask the BirdID classifier whether it is a
    bird. Returns (top1 species name, confidence percent 0-100); ("", 0.0)
    on any failure (model missing, load error) so the caller degrades
    gracefully.
    """
    try:
        adapter = _get_rescue_birdid()
        res = adapter.identify(image, top_k=1,
                               bbox=tuple(int(v) for v in xyxy))
    except Exception:
        return "", 0.0
    if not res:
        return "", 0.0
    top = res[0]
    return (top.name_zh or top.name_en or ""), top.confidence * 100.0


def _rescue_scan(model, image: np.ndarray, accept_conf: float,
                 birdid_gate: int, dir, i18n) -> Optional[dict]:
    """
    无鸟补救扫描：1024px 低阈值重扫 + BirdID 分类器守门。

    第一遍 640 检测低于 UI 阈值时调用。规则：
    1. 重扫最佳 bird 置信度 >= accept_conf → 直接救回；
    2. 否则取最佳候选框（弱 bird，或 airplane/kite 混淆类）交 BirdID 确认，
       top1 置信度 >= birdid_gate(%) → 救回；
    3. 都不满足 → None，维持原拒绝结果。

    参数:
    model: 共享的 YOLO 模型实例（调用方已持有 yolo_infer_lock）
    image (np.ndarray): 已预处理 BGR 图（长边 1024）
    accept_conf (float): UI「AI 置信度」阈值 (0-1)
    birdid_gate (int): 弱候选识鸟确认门槛（百分比 0-100）
    dir: 日志目录
    i18n: I18n 实例（可为 None）

    返回:
    Optional[dict]: 救回时含 xyxy/conf/mask/source/species/species_conf，
                    否则 None

    No-bird rescue scan: high-res low-threshold rescan with the BirdID
    classifier as gatekeeper. Returns the rescued candidate dict or None.
    """
    t = i18n.t if i18n else get_i18n().t
    try:
        from config import get_best_device
        device = get_best_device()
        results = model(image, imgsz=config.ai.RESCUE_IMGSZ,
                        conf=config.ai.RESCUE_CONF, device=device.type)
    except Exception:
        return None

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        del results
        return None
    confs = boxes.conf.cpu().numpy()
    clss = boxes.cls.cpu().numpy().astype(int)
    xyxy = boxes.xyxy.cpu().numpy()
    masks_np = None
    if getattr(results[0], "masks", None) is not None:
        masks_np = results[0].masks.data.cpu().numpy()
    del results

    def _mask_of(i: int):
        if masks_np is not None and i < len(masks_np):
            return masks_np[i]
        return None

    def _result(i: int, source: str, species: str = "",
                species_conf: float = 0.0) -> dict:
        return {
            "xyxy": xyxy[i], "conf": float(confs[i]), "mask": _mask_of(i),
            "source": source, "species": species, "species_conf": species_conf,
        }

    # 规则 1：重扫 bird 直接过 UI 阈值 / Rule 1: rescanned bird clears UI threshold
    cand_i, source = None, ""
    bird_ix = np.flatnonzero(clss == config.ai.BIRD_CLASS_ID)
    if bird_ix.size:
        j = int(bird_ix[confs[bird_ix].argmax()])
        if confs[j] >= accept_conf:
            log_message(t("logs.rescue_direct", conf=f"{confs[j]:.2f}"), dir)
            return _result(j, "bird")
        cand_i, source = j, "bird"

    # 规则 2：弱 bird 或 airplane/kite 混淆候选，识鸟守门
    # Rule 2: weak bird or airplane/kite confusable candidate, BirdID-gated
    if cand_i is None:
        conf_ix = np.flatnonzero(
            np.isin(clss, list(config.ai.RESCUE_CONFUSABLE_CLASS_IDS)))
        if conf_ix.size:
            j = int(conf_ix[confs[conf_ix].argmax()])
            cand_i = j
            source = config.ai.RESCUE_CONFUSABLE_CLASS_IDS[int(clss[j])]
    if cand_i is None:
        return None

    species, species_conf = _birdid_confirm(image, xyxy[cand_i])
    if species_conf >= birdid_gate:
        log_message(t("logs.rescue_confirmed", source=source, species=species,
                      conf=f"{species_conf:.0f}"), dir)
        return _result(cand_i, source, species, species_conf)
    return None
```

放置位置：`_get_iqa_scorer` 之后、`detect_and_draw_birds` 之前。`Optional` 已由现有 import 提供（文件头有 `from typing import Optional`；若无则补）。

- [ ] **Step 5: i18n 日志键（本 Task 一起加，`_rescue_scan` 引用了它们）**

`locales/zh_CN.json` 的 `"logs"` 段内追加：

```json
    "rescue_direct": "  补救扫描: 高分辨率重扫检出鸟 (置信度 {conf})",
    "rescue_confirmed": "  补救扫描: {source} 候选经识鸟确认为 {species} ({conf}%) → 判有鸟",
```

`locales/en_US.json` 的 `"logs"` 段内追加：

```json
    "rescue_direct": "  Rescue scan: high-res rescan found a bird (conf {conf})",
    "rescue_confirmed": "  Rescue scan: {source} candidate confirmed as {species} ({conf}%) → bird",
```

注意 JSON 逗号位置；用 Python 校验：
`.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]; print('ok')"`

- [ ] **Step 6: 运行确认通过**

Run: `.venv/bin/python -m pytest test_ai_model_rescue_scan.py -v`
Expected: 4 passed

- [ ] **Step 7: py_compile + commit**

```bash
.venv/bin/python -m py_compile config.py ai_model.py test_ai_model_rescue_scan.py
git add config.py ai_model.py locales/zh_CN.json locales/en_US.json test_ai_model_rescue_scan.py
git commit -m "feat(ai): 补救扫描核心 _rescue_scan(1024重扫+识鸟守门) 与常量/i18n

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `detect_and_draw_birds` 集成补救 + 返回 10 元组

**Files:**
- Modify: `ai_model.py`（`detect_and_draw_birds`：docstring L128-130、触发点 L244 后、三处 return L210/L301/L475）
- Test: `test_ai_model_rescue_scan.py`（追加 1 个集成测试）

**Interfaces:**
- Consumes: Task 3 的 `_rescue_scan`；Task 1 的 `cfg.rescue_scan_enabled`/`cfg.rescue_birdid_gate`。
- Produces（Task 5 依赖）: `detect_and_draw_birds` 返回 **10 元组**
  `(found_bird, bird_result, confidence, sharpness, nima_score, bird_bbox, img_dims, bird_mask, bird_count, rescued)`，`rescued: bool`。

- [ ] **Step 1: 追加失败测试（10 元组 + 补救禁用直通）**

在 `test_ai_model_rescue_scan.py` 末尾追加：

```python
def test_detect_returns_10_tuple_no_bird(tmp_path, monkeypatch):
    """空检测 + 补救关闭 → 10 元组，末位 rescued=False。
    Empty detections with rescue disabled → 10-tuple ending rescued=False."""
    import cv2

    jpg = str(tmp_path / "t.jpg")
    cv2.imwrite(jpg, np.zeros((64, 64, 3), dtype=np.uint8))

    class _Cfg:
        rescue_scan_enabled = False
        rescue_birdid_gate = 10

    monkeypatch.setattr(ai_model, "get_advanced_config", lambda: _Cfg())
    model = FakeModel(np.zeros((0, 4)), [], [])
    result = ai_model.detect_and_draw_birds(
        jpg, model, None, str(tmp_path), [50, 300, 5.0, False], None)
    assert len(result) == 10
    assert result[0] is False and result[9] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_ai_model_rescue_scan.py::test_detect_returns_10_tuple_no_bird -v`
Expected: FAIL（返回 9 元组，`len(result) == 10` 断言失败）

- [ ] **Step 3: 实现集成**

3a. 触发点：`bird_count = len(all_birds)`（L244）之后、「V4.2: 鸟选择策略」（L246）之前插入：

```python
    # V4.6: 无鸟补救扫描——第一遍低于 UI 阈值时触发 1024px 重扫 + 识鸟守门
    # V4.6: No-bird rescue scan — when pass-1 falls below the UI threshold,
    # rescan at 1024px with the BirdID classifier as gatekeeper.
    rescued = False
    _best_pass1 = max((b['conf'] for b in all_birds), default=0.0)
    if _best_pass1 < ai_confidence:
        _adv = get_advanced_config()
        if _adv.rescue_scan_enabled:
            _rescue = _rescue_scan(model, image, ai_confidence,
                                   _adv.rescue_birdid_gate, dir, i18n)
            if _rescue is not None:
                # 用救回候选覆盖第一遍解析结果，后续裁剪/画框/入库全部复用
                # Overwrite the pass-1 parse with the rescued candidate; the
                # rest of the pipeline (crop/draw/DB) is reused unchanged.
                detections = np.array([_rescue["xyxy"]], dtype=np.float64)
                confidences = np.array([_rescue["conf"]], dtype=np.float64)
                class_ids = np.array([float(config.ai.BIRD_CLASS_ID)])
                masks = (_rescue["mask"][None, ...]
                         if _rescue["mask"] is not None else None)
                all_birds = [{
                    'idx': 0,
                    'conf': _rescue["conf"],
                    'bbox': tuple(int(v) for v in _rescue["xyxy"]),
                }]
                bird_count = 1
                rescued = True
```

3b. 三处 return 扩为 10 元：

- L210（设备失败兜底，位于 rescued 赋值之前，用字面量）：
  `return found_bird, bird_result, 0.0, 0.0, None, None, None, None, 0, False`
- L301（无鸟返回）：
  `return found_bird, bird_result, 0.0, 0.0, None, None, None, None, 0, False`
  （此处补救已失败，rescued 必为 False，用字面量更直白）
- L475（最终返回）：
  `return found_bird, bird_result, bird_confidence, bird_sharpness, nima_score, bird_bbox, img_dims, bird_mask, bird_count, rescued`

3c. docstring `Returns` 行（L128-130）更新为 10 元组并注明
`rescued: 是否经补救扫描救回（V4.6 新增）/ whether rescued by the rescue scan`。

- [ ] **Step 4: 运行全部测试确认通过**

Run: `.venv/bin/python -m pytest test_ai_model_rescue_scan.py -v`
Expected: 5 passed

- [ ] **Step 5: py_compile + commit**

```bash
.venv/bin/python -m py_compile ai_model.py test_ai_model_rescue_scan.py
git add ai_model.py test_ai_model_rescue_scan.py
git commit -m "feat(ai): detect_and_draw_birds 集成补救扫描,返回扩为10元组(+rescued)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: photo_processor 适配 10 元组 + 救回豁免置信度门槛

**Files:**
- Modify: `core/photo_processor.py`（两处解构 L1915/L1937；门槛 L1946）

**Interfaces:**
- Consumes: Task 4 的 10 元组。
- Produces: 无新接口；行为变化 = 救回照片不再被 `ai_confidence` 二次拒绝。

- [ ] **Step 1: 修改两处解构**

L1915 现状：
```python
                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count = result
```
改为：
```python
                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count, rescued = result
```

L1937（多鸟 focus refine 分支）现状：
```python
                                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count = refined_result
```
改为：
```python
                                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count, rescued = refined_result
```

- [ ] **Step 2: 修改置信度门槛（L1945-1946）**

现状：
```python
                confidence_threshold = self.settings.ai_confidence / 100.0
                rejected_by_detection = not detected or (detected and confidence < confidence_threshold)
```
改为：
```python
                confidence_threshold = self.settings.ai_confidence / 100.0
                # V4.6: 救回照片已经过两因子核验(YOLO候选+鸟种分类器)，豁免二次
                # 置信度门槛——否则弱候选救回(conf≈0.3)会被默认0.5阈值再杀一遍。
                # V4.6: Rescued photos passed two-factor verification (YOLO
                # candidate + species classifier); exempt them from this gate,
                # otherwise the default 0.5 threshold would re-kill weak rescues.
                rejected_by_detection = not detected or (
                    detected and not rescued and confidence < confidence_threshold)
```

- [ ] **Step 3: 全链路复读**

通读 L1908-1990，确认 `rescued` 在两条路径（主路径/refine 路径）都必然被赋值后才进入 L1946；确认无其它 9 元解构遗漏：

Run: `grep -n "= result$\|= refined_result$" core/photo_processor.py`
Expected: 仅上述两行（均已带 rescued）。

Run: `grep -rn "detect_and_draw_birds" --include="*.py" . | grep -v ".venv\|docs\|scripts_dev"`
Expected: 仅 `ai_model.py`（定义）与 `core/photo_processor.py`（import + L1488 调用）。

- [ ] **Step 4: py_compile + 已有测试回归 + commit**

```bash
.venv/bin/python -m py_compile core/photo_processor.py
.venv/bin/python -m pytest test_ai_model_rescue_scan.py test_advanced_config_rescue.py -v
git add core/photo_processor.py
git commit -m "feat(processor): 适配检测10元组,救回照片豁免二次置信度门槛

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 设置中心「精选」页开关 + i18n 标签

**Files:**
- Modify: `ui/settings_center.py`（`_build_culling_page` L557-561 之后；`_save_culling` L1135 附近）
- Modify: `locales/zh_CN.json`、`locales/en_US.json`（`"settings"` 段，`culling_flight_label` L593 附近）

**Interfaces:**
- Consumes: Task 1 的 `cfg.rescue_scan_enabled` / `cfg.set_rescue_scan_enabled`。

- [ ] **Step 1: i18n 标签**

`locales/zh_CN.json` `"settings"` 段（`culling_flight_label` 附近）追加：
```json
    "culling_rescue_label": "无鸟补救扫描（更慢但更少漏检）",
```
`locales/en_US.json` 同位置：
```json
    "culling_rescue_label": "Rescue scan for missed birds (slower, fewer misses)",
```

校验 JSON：
`.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]; print('ok')"`

- [ ] **Step 2: 加开关控件**

`_build_culling_page`，`self._cull_burst_folders` 块（L557-561）之后、`fps_row`（L563）之前插入（与 `_cull_flight` 同一套样式惯例）：

```python
        # 无鸟补救扫描 (V4.6): 判无鸟/低置信度时 1024px 重扫 + 识鸟守门
        # No-bird rescue scan (V4.6): 1024px rescan + BirdID gate on rejects
        self._cull_rescue = QCheckBox(self.i18n.t("settings.culling_rescue_label"))
        self._cull_rescue.setChecked(cfg.rescue_scan_enabled)
        self._cull_rescue.setStyleSheet(self._checkbox_qss())
        lay.addWidget(self._cull_rescue)
```

- [ ] **Step 3: 保存接线**

`_save_culling`（`cfg.set_burst_group_folders(...)` L1137 之后）追加：

```python
        cfg.set_rescue_scan_enabled(self._cull_rescue.isChecked())
```

- [ ] **Step 4: 验证**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
```

有既有设置中心测试则一并跑：
`.venv/bin/python -m pytest $(ls test_settings_center*.py 2>/dev/null) -v`（无匹配文件则跳过）
注意既有教训：构造 MainWindow 的测试会切全局 i18n 语言，若跑 UI 测试须确认其自钉 locale。

- [ ] **Step 5: Commit**

```bash
git add ui/settings_center.py locales/zh_CN.json locales/en_US.json
git commit -m "feat(ui): 设置中心精选页新增无鸟补救扫描开关

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 全链路验证（真实模型 + 39 张漏检样本）

**Files:**
- Create: `scripts_dev/validate_rescue_scan.py`（验证脚本，入库便于回归复用）

**前置条件:** 本机存在 `/Users/jameszhenyu/Desktop/零星`（39 张已确认有鸟的漏检 ARW）与 `models/yolo11l-seg.pt`。目录不存在时脚本明确报错退出，不静默通过。

- [ ] **Step 1: 写验证脚本**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无鸟补救扫描全链路验证：对 39 张已确认有鸟的漏检 ARW 样本，
经真实 detect_and_draw_birds 验证救回率与开关行为。

预期（依据 2026-07-14 A/B 实测）:
- rescue_scan_enabled=True  → 检出(含救回) >= 30/39
- rescue_scan_enabled=False → 与旧版行为一致，检出 <= 5/39

End-to-end validation of the rescue scan on 39 confirmed missed-bird ARW
samples through the real detect_and_draw_birds; asserts rescue rate and
toggle behavior.
"""
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "/Users/jameszhenyu/Desktop/零星"
UI_SETTINGS = [50, 300, 5.0, False]  # 置信度50/锐度/美学/不存裁切


def extract_jpeg(arw: str, out_dir: str) -> str:
    """exiftool 抽内嵌 JPEG（与生产 -JpgFromRaw 链路一致）。"""
    jpg = os.path.join(out_dir, os.path.splitext(os.path.basename(arw))[0] + ".jpg")
    for tag in ("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"):
        with open(jpg, "wb") as f:
            subprocess.run(["exiftool", tag, "-b", arw], stdout=f,
                           stderr=subprocess.DEVNULL)
        if os.path.getsize(jpg) > 10000:
            return jpg
    raise RuntimeError(f"no embedded jpeg: {arw}")


def main() -> None:
    if not os.path.isdir(SAMPLE_DIR):
        sys.exit(f"样本目录不存在: {SAMPLE_DIR}")

    from advanced_config import get_advanced_config
    from ai_model import load_yolo_model, detect_and_draw_birds

    cfg = get_advanced_config()
    saved = cfg.rescue_scan_enabled
    model = load_yolo_model()
    arws = sorted(os.path.join(SAMPLE_DIR, f) for f in os.listdir(SAMPLE_DIR)
                  if f.lower().endswith(".arw"))
    print(f"samples: {len(arws)}")

    with tempfile.TemporaryDirectory() as tmp:
        jpgs = [extract_jpeg(a, tmp) for a in arws]
        for enabled, expect in ((False, "<=5"), (True, ">=30")):
            cfg.set_rescue_scan_enabled(enabled)
            hits = rescued_n = 0
            for jpg in jpgs:
                r = detect_and_draw_birds(jpg, model, None, tmp, UI_SETTINGS, None)
                # 与生产二次门槛同口径:救回豁免,否则须过 UI 阈值
                # Same gate as production: rescued exempt, else UI threshold.
                ok = r[0] and (r[9] or r[2] >= UI_SETTINGS[0] / 100.0)
                hits += int(ok)
                rescued_n += int(bool(r[9]))
            print(f"rescue_enabled={enabled}: 检出 {hits}/{len(jpgs)} "
                  f"(其中救回 {rescued_n}) 预期 {expect}")
            if enabled:
                assert hits >= 30, f"救回率不达标: {hits}/39"
            else:
                assert hits <= 5, f"关闭开关行为异常: {hits}/39"

    cfg.set_rescue_scan_enabled(saved)
    print("VALIDATION PASSED")


if __name__ == "__main__":
    main()
```

注意：脚本结尾恢复了用户原开关值，但**不调用 `cfg.save()`**（set_* 已按现有实现决定是否持久化；执行前记录 `advanced_config.json` 的 mtime，跑完确认未意外污染用户配置——若 set_* 会 save，则脚本改为用 `AdvancedConfig(config_file=临时路径)` 打桩 `ai_model.get_advanced_config` 的返回，绝不动真实配置）。

- [ ] **Step 2: 运行验证**

Run: `.venv/bin/python scripts_dev/validate_rescue_scan.py`
Expected 输出（数值依据 A/B 实测，允许小幅波动但断言必须过）:
```
samples: 39
rescue_enabled=False: 检出 0-5/39 (其中救回 0) 预期 <=5
rescue_enabled=True: 检出 >=30/39 (其中救回 >=26) 预期 >=30
VALIDATION PASSED
```

- [ ] **Step 3: 最低验证清单（CLAUDE.md 要求）**

```bash
.venv/bin/python -m py_compile advanced_config.py ai_model.py config.py \
  core/photo_processor.py ui/settings_center.py birdid/bird_identifier.py
.venv/bin/python -m pytest test_advanced_config_rescue.py \
  test_birdid_classifier_lock.py test_ai_model_rescue_scan.py -v
```
Expected: 全绿。

- [ ] **Step 4: 提交验证脚本**

```bash
git add scripts_dev/validate_rescue_scan.py
git commit -m "test(rescue): 39张漏检样本全链路验证脚本(救回率+开关回归)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: 人工验收项（报告给用户，不自动化）**

- GUI 跑一次真实批处理（含识鸟开启），确认补救日志出现、无 MPS 并发崩溃（分类器锁生效）。
- 可选：对 1063 张测试集全量重跑对比（spec 验证 #2：判无鸟 75→≤41，604 张 1-3 星检出不变）。

---

## Self-Review 记录

- **Spec 覆盖**：3.1 流程/触发两类漏检→Task 3+4；3.2 代码落点（含 10 元组、门槛豁免、推理锁、常量）→Task 2/3/4/5；3.3 设置 SSOT→Task 1/6；3.4 日志 i18n→Task 3（report_db `rescued` 字段按 spec 为可选项，判定为 schema 无低成本携带位则不做，符合 spec）；3.5 不做项无需任务；第 5 节验证→Task 7。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`_rescue_scan` 返回 dict 键（xyxy/conf/mask/source/species/species_conf）在 Task 3 定义与 Task 4 消费一致；10 元组顺序在 Task 4 定义与 Task 5/7 消费一致；`rescue_scan_enabled`/`rescue_birdid_gate` 命名全程一致。
