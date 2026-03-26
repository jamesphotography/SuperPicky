# SuperPicky - AI Bird Photo Culling Tool 🦅

[![Version](https://img.shields.io/badge/version-4.1.0-blue.svg)](https://github.com/jamesphotography/SuperPicky)
[![Platform](https://img.shields.io/badge/platform-macOS%20|%20Windows-lightgrey.svg)](https://github.com/jamesphotography/SuperPicky/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Build](https://github.com/jamesphotography/SuperPicky/actions/workflows/build-release.yml/badge.svg)](https://github.com/jamesphotography/SuperPicky/actions/workflows/build-release.yml)

[**中文文档 (Chinese)**](README_zh.md) | [**Release Notes**](RELEASE_NOTES.md)

**Smart AI Culling Tool for Bird Photographers**

Shoot freely, cull easily! A smart photo culling software designed specifically for bird photographers. It uses multi-model AI technology to automatically identify, rate, and filter bird photos, significantly improving post-processing efficiency.

---

## 🛠️ Installation for Developers

To run SuperPicky from source or build it yourself, you must first download the required AI models:

```bash
git clone https://github.com/jamesphotography/SuperPicky.git
cd SuperPicky
pip install -r requirements.txt
python scripts/download_models.py
```

---

## 🌟 Core Features

### 🤖 Multi-Model Synergy
- **YOLO11 Detection**: Precise bird detection and segmentation masks.
- **[SuperEyes](https://github.com/triple333sR9/SuperBirdEye)**: Detects eye visibility and calculates head sharpness.
- **[SuperFlier](https://github.com/triple333sR9/SuperFlier)**: Identifies flight poses for bonus points.
- **TOPIQ Aesthetics**: Assesses overall image aesthetics, composition, and lighting.

### ⭐ Smart Rating System (0-3 Stars)
| Stars | Condition | Meaning |
|-------|-----------|---------|
| ⭐⭐⭐ | Sharpness OK + Aesthetics OK | Excellent, worth editing |
| ⭐⭐ | Sharpness OK OR Aesthetics OK | Good, consider keeping |
| ⭐ | Bird found but below threshold | Average, usually delete |
| 0 | No bird / Poor quality | Delete |

### ⚙️ Skill Level Presets (New)
Automatically set thresholds based on your experience:
- **🐣 Beginner**: Sharpness>300, Aesthetics>4.5 (Keep more)
- **📷 Intermediate**: Sharpness>380, Aesthetics>4.8 (Balanced)
- **👑 Master**: Sharpness>520, Aesthetics>5.5 (Strict)

### 🏷️ Special Tags
- **Pick (Flag)**: Top 25% intersection of sharpness & aesthetics among 3-star photos.
- **Flying**: Green label for bird-in-flight photos.
- **Exposure**: Filters over/under-exposed shots (Optional).

### 📂 Auto-Organization
- **Sort by Stars**: Auto-move to 0star/1star/2star/3star folders.
- **EXIF Write**: Writes ratings, flags, and scores to RAW metadata.
- **Lightroom Compatible**: Sort and filter immediately after import.
- **Undo**: One-click reset to restore original state.

---

## 📥 Downloads

### macOS
**Apple Silicon (M1/M2/M3/M4) (v4.1.0 LTS)**
- [GitHub Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_arm64_c869d64.dmg)
- [Google Drive (Mirror)](https://drive.google.com/file/d/1odYNFvtYZa8pAO_bYZZCh5FZ6v0ggxFQ)
- [Baidu Netdisk](https://pan.baidu.com/s/1xzex0UrSDiZeWyLuYRSqNg?pwd=t6c4) Code: t6c4
- [Quark](https://pan.quark.cn/s/625a2dac438a)

**Intel (Pre-2020 Mac) (v4.1.0 LTS)**
- [GitHub Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_Intel_c869d64.dmg)
- [Google Drive (Mirror)](https://drive.google.com/file/d/1dPdCoObVLuxy9ks_sYjbfSR4bI3A-IPD)
- [Baidu Netdisk](https://pan.baidu.com/s/1lNz2mBUEee8qqrd95rPJsA?pwd=3821) Code: 3821
- [Quark](https://pan.quark.cn/s/1b5d87b74059)

### Windows
**CUDA-GPU Version (v4.1.0 Beta)**
- [Baidu Netdisk](https://pan.baidu.com/s/1XBaGXPim_WzjpNBgG-altg?pwd=c2a6) Code: c2a6
- [Google Drive (Mirror)](https://drive.google.com/file/d/1IKSxB3KbQdDO7VhnsGnHjOb2EgqZIgSB/view?usp=sharing)
- [Quark](https://pan.quark.cn/s/d15276717367)

**CPU Version (v4.1.0 LTS)**
- [GitHub Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_Setup_Win64_4.1.0_242f4be.exe)
- [Baidu Netdisk](https://pan.baidu.com/s/1dle-dGbKx5_On5cfdaaLXQ?pwd=872v) Code: 872v
- [Google Drive (Mirror)](https://drive.google.com/file/d/1nTcgQdUqotu04kVkWUZqENnQtn573uzx)
- [Quark](https://pan.quark.cn/s/1b7016c16f79)


---

## 🚀 Quick Start

1. **Select Folder**: Drag & drop or browse for a folder with bird photos.
2. **Adjust Thresholds** (Optional): Sharpness (200-600), Aesthetics (4.0-7.0).
3. **Toggle Features**: Flight detection, Exposure check.
4. **Start**: Click to begin AI processing.
5. **Review**: Photos are organized; import to Lightroom to see ratings.

---

## 📝 Update Log

### v4.0.5 (2026-02-15)
- 🚀 **Architecture**: SQLite migration, ~1.9x speedup.
- 🌟 **Community**: Thanks @OscarKing888 for Sony ARW & UTF-8 fixes.
- 🧹 **Clean**: Unified temp files to hidden cache dir.
- 🔧 **Fixes**: Chinese path support, ExifTool deadlock, Plugin metadata.

---

## 🐦 Species Naming Standards (AviList Mapping)

SuperPicky supports multiple English naming standards for bird species via the **AviList v2025** mapping. Choose your preferred format in **Settings > Culling Criteria > Species Name Format**:

| Format | Source |
|--------|--------|
| Default (OSEA Model) | Original model training names |
| AviList v2025 | AviList unified English names |
| Clements / eBird v2024 | Cornell/eBird taxonomy |
| BirdLife v9 | BirdLife International |
| Scientific Name Only | Binomial nomenclature |

**Updating AviList:** The mapping is built from `AviList-v2025-11Jun-extended.xlsx` (located in `scripts_dev/`) using an offline build script. When a new AviList version is released (typically annually), replace the xlsx file in `scripts_dev/` and re-run:

```bash
pip install openpyxl  # first time only
python scripts_dev/build_avilist_mapping.py
```

Review unmatched species in the report output and add manual overrides to `scripts_dev/avilist_manual_overrides.json` if needed.

---

## 📄 License

Open sourced under **GNU AFFERO GENERAL PUBLIC LICENSE Version 3**.

This project uses:
- **YOLO11** by Ultralytics
- **OSEA** by Sun Jiao (github.com/sun-jiao/osea)
- **[SuperEyes (SuperBirdEye)](https://github.com/triple333sR9/SuperBirdEye)** by [Jordan Yu (于若君)](https://github.com/triple333sR9)
- **[SuperFlier](https://github.com/triple333sR9/SuperFlier)** by [Jordan Yu (于若君)](https://github.com/triple333sR9)
- **TOPIQ** by Chaofeng Chen et al.
- **AviList**: AviList Core Team. 2025. AviList: The Global Avian Checklist, v2025. https://doi.org/10.2173/avilist.v2025 — Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
