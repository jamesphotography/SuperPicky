# SuperPicky — Model Replication Report

> **Audience:** an ML scientist reproducing every model from scratch.
> **Scope:** the models and the signal-processing immediately around them —
> input pre-processing, output post-processing, architecture, hyper-parameters,
> weights I/O, and the deterministic score fusion. UI/app plumbing is excluded
> except for §10, which documents the user controls that tune model behavior.
> Every fact carries a `file:line` reference.

---

## 0. System overview

A still photo runs through **five neural networks** + **two classical
CV/EXIF analyzers**; their numeric outputs are fused by a rule engine into a
0–3 star rating. Species ID (Model 5) runs in parallel for labelling. Models 1
and 4 are reused by a video pipeline.

| # | Model | Architecture | Weights | Size | Task |
|---|-------|--------------|---------|------|------|
| 1 | Bird detector/segmenter | YOLO11-large-seg | `models/yolo11l-seg.pt` | 56 MB | bbox + instance mask |
| 2 | Keypoint localizer | ResNet50 + MLP head | `models/cub200_keypoint_resnet50_slim.pth` | 99 MB | eye/beak landmarks → head sharpness |
| 3 | Aesthetic scorer (IAA) | TOPIQ `CFANet` (ResNet50) | `models/cfanet_iaa_ava_res50-3cd62bb3.pth` | 294 MB | aesthetic MOS 1–10 |
| 4 | Flight classifier | EfficientNet-B3 (binary) | `models/superFlier_efficientnet.pth` | 43 MB | in-flight prob 0–1 |
| 5 | Species classifier | ResNet34 (OSEA) | `models/model20240824.pth` / `birdid/models/birdid2024.pt.enc` | 108 / 23 MB | species among 10,964 |
| 6a | Exposure analyzer | histogram (no NN) | — | — | over/under-exposure flags |
| 6b | Focus-point analyzer | EXIF MakerNote (no NN) | — | — | AF-point location → weights |

Sizes are on-disk weights. **Device:** macOS→MPS else CPU; other→CUDA else CPU,
CPU fallback on error (`config.py:672-701`). Models 2 & 3 run **FP16** on
MPS/CUDA (`core/keypoint_detector.py:126-130`, `iqa_scorer.py:62-66`).
**Deps:** `torch==2.8.0` (mac) / `torch==2.7.1+cu118` (CUDA);
`ultralytics>=8.0.0`, `timm>=0.9.0`, `opencv-python`, `Pillow`, `rawpy`,
`cryptography` (`requirements_mac.txt`, `requirements_cuda.txt`,
`requirements_base.txt`).

---

## 1. Model 1 — Bird detector & segmenter (YOLO11-large-seg)

**Purpose.** Pipeline gate: is there a bird, where, and a pixel-accurate mask.
The `-seg` variant is required because head-sharpness (§2) and focus checks (§6b)
intersect their region of interest with the bird silhouette, not the bbox. Stock
Ultralytics checkpoint, COCO taxonomy, **bird = class 14**
(`config.py:362`; reused at `birdid/bird_identifier.py:362`), loaded via
`ultralytics.YOLO` (`ai_model.py:5,23`).

**Defaults:** input longest-side **1024** px (`config.py:363`); internal conf
threshold **0.25** (Ultralytics default, `ai_model.py:242`); mask binarize
**>0.5**; no manual mean/std (Ultralytics normalizes internally).

**Pre-processing** (`preprocess_image`, `ai_model.py:55-64`): `cv2.imread`→BGR
uint8; isotropic resize to longest-side 1024 (`cv2.INTER_AREA`); array passed
straight to `model(image, device=...)` (`:138`). Inputs are JPEG; RAW is
converted to an embedded-preview JPEG upstream (`raw_to_jpeg`,
`tools/find_bird_util.py:48-99`). The UI `ai_confidence` slider only filters
*after* detection — it does not change YOLO's threshold (`ai_model.py:96`).

**Inference & parsing.** Device inference with CPU-retry on failure
(`:138-154`); tensors pulled to numpy and `results` deleted to bound VRAM
(`:181-191`). Extracts `boxes.xyxy/conf/cls` and `masks.data` (`:181-188`).

**Bird selection** (`:195-229`): 1 bird → it; N + AF point → bird whose bbox
holds the focus pixel, else highest conf; N, no AF → highest conf.

**Mask** (`:423-431`): nearest-resize to image size, `(mask>0.5)*255`; later
upscaled to original resolution (`INTER_NEAREST`,
`core/photo_processor.py:1835-1844`).

**Return** (`:436`): 9-tuple; its `sharpness`/`nima` fields are placeholders —
the real values come from Models 2 & 3 later (`:262-264,309-311`).

---

## 2. Model 2 — Keypoint localizer (ResNet50, CUB-200)

**Purpose.** Quality hinges on **eye sharpness**, not frame sharpness. Predicts
3 keypoints (left eye, right eye, beak) + per-point visibility so the pipeline
can reject head-occluded angles and measure sharpness only inside a head circle.
Trained on **CUB-200** (`core/keypoint_detector.py:1-4,82`).

**Defaults:** input **416×416**, ImageNet norm; visibility threshold **0.3**
(`:72`); head radius = eye–beak dist **×1.2**, or **0.15×bbox** when no beak
(`:73-74`); FP16 on GPU; `num_parts=3, hidden_dim=512, dropout=0.2` (`:40`).

**Architecture** (`PartLocalizer`, `:38-64`):
```
backbone : resnet50(weights=None), fc→Identity                     (:43-45)
head     : Linear(2048→512)→BN→ReLU→Dropout(0.2)
           Linear(512→256) →BN→ReLU→Dropout(0.2)                   (:47-56)
coord_head: Linear(256→6) →sigmoid →(N,3,2) normalized [0,1]       (:57,62)
vis_head : Linear(256→3) →sigmoid →(N,3)                           (:58,63)
```
Retraining target: normalized coords (MSE/L1) + visibility (BCE).

**Pre-processing** (`:100-104`) on the YOLO crop (RGB→PIL):
`Resize(416²)→ToTensor→Normalize(ImageNet)`; FP16 + `inference_mode` (`:157-161`).

**Post-processing.** Visible iff `vis≥0.3` (`:177-179`);
`all_keypoints_hidden` (all<0.3, `:184`) → 1-star cap; `best_eye_visibility =
max(left,right)` (`:205`) drives rating de-weighting (§7).

**Head-sharpness** (`_calculate_head_sharpness`, `:221-313`) — the model's real
output: pick eye farther from beak (fallback to better eye ×0.8 penalty if both
eyes hidden, `:244-263`); build a head circle, **AND** with the seg mask so
background can't inflate it (`:298-308`); metric = **Tenengrad** (Sobel² energy)
then log-normalize to 0–1000 with `MIN_VAL=100`, `MAX_VAL=154016`
(`:332-357`). Tenengrad chosen over Laplacian for noise robustness (`:319`).

**Weights** (`:115-120`): `torch.load(weights_only=True)`, accepts raw or
`{'model_state_dict':...}`.

---

## 3. Model 3 — Aesthetic scorer (TOPIQ `CFANet`, AVA/IAA)

**Purpose.** Perceptual **aesthetic MOS (1–10)** to separate good from great once
sharpness/visibility gates pass. Replaces NIMA; TOPIQ's top-down attention reads
the subject better, ~40% faster, lighter ResNet50 backbone (`iqa_scorer.py:6-11`,
`topiq_model.py:1-16`). Checkpoint = IAA-on-**AVA**
`cfanet_iaa_ava_res50-3cd62bb3.pth` (`:476`) from IQA-PyTorch (Chen et al., IEEE
TIP 2024, `:5-8`); CFANet re-implemented standalone to drop `pyiqa`.

**Defaults** (constructor, `:207-219`): `resnet50` backbone (timm `features_only`),
`num_class=10` (AVA bins), `inter_dim=512`, `num_heads=4`, `num_attn_layers=1`,
`dprate=0.1`, `activation='gelu'`. Input **384×384**, `ToTensor` only, output
clamped **[1,10]**.

**Architecture** (`CFANet`, `:195-318`): per-level GatedConv weighted pool
(`:172-192`) + 1×1 dim-reduce→512 + self-attention `TransformerEncoder`
(`:256-268`); cross-scale `TransformerDecoder` fuses coarse→fine (`:271-281,
384-388`); learned 32×32 pos-embeddings (`:307-311`); score head
`LayerNorm→Linear→GELU→…→Linear(→10)→Softmax` → 10-bin distribution (`:294-304`).

**Pre/in/post.** Resize **384²** LANCZOS (`iqa_scorer.py:107-108,155-160`; fixed
for MPS `adaptive_avg_pool2d`); **`ToTensor` only — ImageNet norm is applied
*inside* `CFANet.preprocess`** (`:236-237,325-327`), do not double-normalize;
FP16 + `inference_mode`; `dist_to_mos` = `Σ pᵢ·i, i=1..10` (`:56-70,406-407`);
clamp [1,10] (`iqa_scorer.py:126-127`).

**Weights** (`load_topiq_weights`, `:430-465`): `weights_only=True`; unwrap
`{'params':...}` (`:452-453`); strip `module.` (`:421-427`); `strict=False`.

---

## 4. Model 4 — Flight classifier (EfficientNet-B3, binary)

**Purpose.** Binary in-flight vs perched. Flight keepers are rewarded in fusion
(sharpness ×1.2, aesthetic ×1.1; §7). `superFlier`, EfficientNet-B3
(`core/flight_detector.py:1-9`).

**Defaults:** input **384×384** (`:42`), ImageNet norm; decision threshold
**0.5** (`:43`); classifier dropout **0.2**; `detect_batch` size **8** (`:188`).

**Architecture** (`_build_model`, `:84-103`):
```
efficientnet_b3(weights=None); classifier →
    Dropout(0.2) → Linear(in_features,1) → Sigmoid          (:88-101)
```
Single-logit sigmoid; retrain with BCE on flight/perched crops.

**Pre/in/post** (`:75-82,133-186`): `Resize(384²)→ToTensor→Normalize(ImageNet)`;
accepts path/PIL/numpy (numpy assumed **BGR**→RGB, `:159-165`); in the pipeline
the input is a square-padded single-bird crop (`core/flight_adapter.py:8-12`);
`no_grad`; `is_flying = prob > 0.5` (`:179-183`).

---

## 5. Model 5 — Species classifier (ResNet34, OSEA, 10,964 species)

**Purpose.** Species labelling. Based on open **OSEA**
(`birdid/osea_classifier.py:1-11`), plain torchvision ResNet34. Two entry points:
`osea_classifier.py` (reference) and `bird_identifier.py` (production: YOLO
pre-crop, GPS/eBird filtering, encrypted-weights fallback).

**Defaults:** head width **11000**, valid species **10,964** (logits sliced,
`bird_identifier.py:817-818`); input **224²**; ImageNet norm; **temperature 0.9**
(production, `:820`) / 1.0 (reference, `osea_classifier.py:224`); confidence floor
**1.0%** (un-filtered) / **0.3%** (region-filtered) (`:830`); `top_k=5`.

**Architecture & weights** (`:295-313`): `resnet34(num_classes=11000)` +
`load_state_dict`. Class-id→name from `birdid/data/bird_reference.sqlite`
(`osea_classifier.py:199-218`). Weights resolution
(`bird_identifier.py:223-258`): (1) plain `model20240824.pth`; (2) AES-encrypted
TorchScript `birdid2024.pt.enc`; (3) legacy TorchScript. Decryption
(`decrypt_model`, `:188-215`): layout `salt(16)‖iv(16)‖ciphertext`,
**PBKDF2-HMAC-SHA256 ×100,000 → 32-byte key**, **AES-CBC**, PKCS7 unpad; loaded
via `torch.jit.load` (`:218-220`).

**Pre-processing** — two transforms by crop provenance (`:777-794`):
```
OSEA_TRANSFORM        (full image):  Resize(256)→CenterCrop(224)→ToTensor→Norm
OSEA_TRANSFORM_DIRECT (YOLO crop):   Resize(224²,LANCZOS)→ToTensor→Norm
```
Selected by `is_yolo_cropped` (`:810`); center-crop adds ~15% confidence
(`osea_classifier.py:8-10`). Loading is **orientation-aware** (`load_image`,
`:447-529`): RAW via rawpy thumb or `postprocess(use_camera_wb, output_bps=8,
half_size)`; EXIF/libraw orientation applied **before** `convert('RGB')`
(`_auto_orient`, `:416-444`) — fixes sideways portrait RAWs.

**Optional YOLO pre-crop** (`:313-413`): conf 0.25, class 14, highest-conf box,
square-expand `padding=0.15`, black-pad to square; only when long side >640
(`:941-943`).

**Inference & post** (`predict_bird`, `:797-908`): slice to 10,964; softmax at
T=0.9; top-100-then-filter when a species set is active, else top-k; confidence
floor as above; region filter keeps only in-set class_ids (`:872-877`) with
country→global fallback (`:1030-1066`). **Geo/rarity enrichment** (`:911-1074`):
EXIF GPS→ISO country (offline `reverse_geocoder`, `:58-80`), AVONET/eBird species
set (`birdid/avonet_filter.py:237-318`), IUCN + country-aware GBIF rarity
(`:879-888`).

---

## 6. Models 6a/6b — classical analyzers (no NN)

**6a Exposure** (`core/exposure_detector.py:72-122`): grayscale→256-bin
histogram. **Defaults:** over-exposed if pixels ≥**235** exceed **10%**;
under-exposed if pixels ≤**15** exceed **10%** (`:53-57`). → one-star downgrade
(§7).

**6b Focus point** (`core/focus_point_detector.py`): parses MakerNote AF data via
resident ExifTool (`:681-690`) per brand (Nikon/Sony/Canon/Olympus/Fujifilm/
Panasonic, `:64-135,176-679`); normalizes AF point to [0,1] with DX-crop
(`:692-766`) and orientation (`:768-789`) correction. `verify_focus_in_bbox`
(`:792-856`) → two multiplicative weights:

| AF point lands | sharpness w | aesthetic w |
|---|---|---|
| in head circle | 1.1 | 1.0 |
| in seg mask | 0.9 | 1.0 |
| in bbox | 0.8 | 0.9 |
| outside bbox | 0.5 | 0.8 |
| not focused / no data | 0.8 / 1.0 | 0.9 / 1.0 |

---

## 7. Output fusion — rating engine

`RatingEngine.calculate` (`core/rating_engine.py:101-271`) — pure rules, fully
reproducible. **Default thresholds** (`:68-77`): `min_confidence=0.50`,
`min_sharpness=100`, `min_nima=3.5`, `sharpness_threshold=400`,
`nima_threshold=5.0`.

Order: 1) no bird → **-1** (`:142`); 2) conf<0.50 → **0** (`:150`);
3) all keypoints hidden → **1** (`:158`); 4) sharpness<100 → **0** (`:166`);
5) TOPIQ<3.5 → **0** (`:174`); 6) apply focus weights (`:194-195`); 7) flight
bonus ×1.2/×1.1 (`:198-201`); 8) base star: both gates→**3**, one→**2**,
neither→**1** (`:216-231`); 9) eye-visibility de-weight
`round(base·clip(best_eye×2,0.5,1.0))` (`:236-237`); 10) exposure issue → −1 star
(`:240-241`).

Upstream, `photo_processor` also applies **ISO sharpness normalization**
`factor=max(0.5, 1−0.05·log2(ISO/800))` for ISO>800
(`:455-474`; `ISO_BASE=800`, `PENALTY=0.05`, `MIN=0.5`).

**Orchestration** (`core/photo_processor.py`): detect+crop (`:1348,1673`) →
upscale mask (`:1835-1844`) → flight (`:1850,1946`) → keypoints+head sharpness
(`:1856-1870`) → TOPIQ (`:1921`) → exposure (`:1962`) → focus weights (`:2074`)
→ rating (`:2009,2119`); species ID in parallel via `identify_bird(preloaded_crop)`
(`:1081-1114`).

---

## 8. Video pipeline reuse

`core/video_analyzer.py` reuses **Model 1** for per-frame bird detection
(`:194-239`) and optionally attaches **Model 5** and **Model 4** (via
`core/flight_adapter.py`) through the `BirdClassifier`/`FlightClassifier`
interfaces. **Defaults:** YOLO threshold **0.5**, min **2** frames/segment
(`:208,218-219`). The flight adapter square-crops each bbox before EfficientNet-B3
(`flight_adapter.py:54-66`).

---

## 9. Reproduction checklist (from blank)

1. **Env:** Python 3.12/3.13, `requirements_base.txt` + platform torch (2.8.0 mac
   / 2.7.1+cu118 CUDA); `ultralytics`, `timm`, `opencv-python`, `Pillow`,
   `rawpy`, `cryptography`.
2. **YOLO:** fine-tune `yolo11l-seg` on COCO-format birds (class 14) →
   `models/yolo11l-seg.pt`. Serve at input 1024, conf 0.25, mask>0.5.
3. **Keypoints:** train `PartLocalizer` (§2 head) on CUB-200 {eyes,beak} →
   normalized coords + visibility. Input 416², ImageNet norm.
4. **TOPIQ:** take IAA-AVA ResNet50 from IQA-PyTorch (or retrain CFANet on AVA,
   10-bin EMD). Input 384², `ToTensor` only (norm internal).
5. **Flight:** train EfficientNet-B3 (Dropout0.2→Linear(1)→Sigmoid), BCE on
   flight/perched. Input 384², ImageNet norm, threshold 0.5.
6. **Species:** train ResNet34 (head 11000, 10,964 valid) on OSEA. Input 224²,
   ImageNet norm, inference T=0.9; ship `bird_reference.sqlite`. Optionally
   AES-CBC/PBKDF2-encrypt as TorchScript (§5).
7. **6a/6b:** no training — use the constants in §6.
8. **Fusion:** implement §7 verbatim (deterministic).

---

## 10. UI parameters that tune model behavior

User controls only adjust **thresholds, multipliers, enable-flags, and filtering
scope** — they never retrain or alter weights. Defaults below are needed to
reproduce system behavior.

### 10.1 Stills — primary controls (`ui/main_window.py`)

`ui_settings` built at `:2154-2164`, mapped to `ProcessingSettings` at `:338-345`.

| Control | Range / default | Effect |
|---|---|---|
| **Sharpness threshold** | `sharp_slider` **200–600**, default **400** (`:1416-1420`) | 2-/3-star gate for Model 2 head-sharpness (`rating_engine.py:216,220-231`). Only moves the accept bar, not inference. |
| **Aesthetics threshold** | `nima_slider` **4.0–7.0**, default **5.0** (`:1440-1442`) | 2-/3-star gate for Model 3 MOS (`rating_engine.py:217`). |
| **AI confidence** | derived `min_confidence×100`, default **50%** (`:1401`; slider §10.3) | YOLO confidence **reject floor** (`rating_engine.py:150`); does not change YOLO's 0.25 threshold. |
| **Flight detection** | `flight_check`, default on (`:1331-1332`) | Enables Model 4 (`photo_processor.py:958-962`); off → no flight bonus. |
| **Bird ID** | `birdid_check` (`:1363-1377`) | Enables Model 5 (`photo_processor.py:1081-1114`); labelling only. |
| **Burst detection** | `burst_check` (`:1345-1346`) | Groups duplicates for "pick" selection; not a model. |

Exposure (`ui_settings[6]`) is hard-wired **off** (`:344,2161`), so Model 6a's
downgrade is dormant.

### 10.2 Skill-level presets (`ui/skill_level_dialog.py:18-36`)

One card moves both gates at once (`_apply_skill_level_thresholds`,
`ui/main_window.py:769`):

| Preset | sharpness_threshold | nima_threshold | Effect |
|---|---|---|---|
| Beginner | 300 | 4.5 | loosest → more 2–3 star |
| Intermediate (default) | 380 | 4.8 | balanced |
| Master | 520 | 5.5 | strictest |

### 10.3 Advanced — reject floors (`ui/advanced_settings_dialog.py`)

Below these, a frame drops to 0 stars (`create_rating_engine_from_config`,
`rating_engine.py:285-302`).

| Control | Range / default | Effect |
|---|---|---|
| **Detection sensitivity** | 30–70%, **50** (`:197-203`) | `min_confidence` floor (`rating_engine.py:150`); seeds `ai_confidence`. |
| **Sharpness requirement** | 100–500, **100** (`:206-212`) | `min_sharpness` floor (`rating_engine.py:166`). |
| **Aesthetics requirement** | 0.0–5.0, **4.0** (`:215-222`) | `min_nima` floor (`rating_engine.py:174`); **0 disables aesthetic filtering**. |
| **Burst FPS** | 4–20, **10** (`:228-235`) | burst clustering window. |

### 10.4 Advanced — species ID (Model 5)

| Control | Range / default | Effect |
|---|---|---|
| **Bird-ID confidence** | 30–95%, **50** (`:248-255`) | min top-1 confidence (post T=0.9) to **write** a species label (`photo_processor.py:1141-1159`). |
| **Name format** | default/avilist/clements/birdlife/scientific (`:268-274`) | taxonomy for the emitted name (`bird_identifier.py:853-870`); label remap only. |

### 10.5 Bird-ID dock — geo filtering (`ui/birdid_dock.py`)

| Control | Effect on Model 5 |
|---|---|
| **Country/region** (Auto-GPS / Global / list, `:559-581`) | selects AVONET/eBird species set used to filter & re-rank (`bird_identifier.py:982-1016`); region-filtered → top-100, conf floor 0.3% (`:823,830`); Global = no filter. |
| **GPS / eBird** (`:77,83-84`) | toggle GPS reverse-geocoding & eBird filtering (`bird_identifier.py:967-1019`); GPS also drives GBIF rarity. |
| **Top-K** (default 5, `:76,82`) | number of candidates returned. |

**TTA** (orig + h-flip averaging, `osea_classifier.py:284-344`) and softmax
**temperature** exist but are **not UI-exposed** (production T fixed at 0.9).

### 10.6 Video controls (`ui/advanced_settings_dialog.py`)

| Control | Range / default | Effect |
|---|---|---|
| **Species mode** | instant/fast/full (`:782-791`) | frames/segment sent to Models 4/5. |
| **Max sampled frames** | 30–240, **60** (`:796-803`) | frames YOLO scans per clip. |
| **YOLO confidence** | 0.30–0.90, **0.50** (`:808-815`) | per-frame YOLO accept threshold (`video_analyzer.py:208`). |
| **Enable species/flight** | checkboxes (`:822-832`) | attach Models 5/4 to video. |

---

### Appendix — file map

| Concern | File |
|---|---|
| YOLO detect + mask; RAW→JPEG | `ai_model.py`, `tools/find_bird_util.py` |
| Keypoints + head sharpness | `core/keypoint_detector.py` |
| TOPIQ aesthetic | `topiq_model.py`, `iqa_scorer.py` |
| Flight | `core/flight_detector.py`, `core/flight_adapter.py` |
| Species + geo/rarity | `birdid/bird_identifier.py`, `birdid/osea_classifier.py`, `birdid/avonet_filter.py` |
| Exposure / focus | `core/exposure_detector.py`, `core/focus_point_detector.py` |
| Fusion / orchestration | `core/rating_engine.py`, `core/photo_processor.py` |
| Video | `core/video_analyzer.py`, `core/bird_classifier_base.py` |
| Device / AI constants | `config.py` |
