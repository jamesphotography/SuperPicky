<p align="center">
  <img src="img/icon.png" width="96" alt="SuperPicky icon">
</p>

<h1 align="center">SuperPicky</h1>

<p align="center">
  <b>Smart bird photo culling for Mac and Windows · free and open source</b><br>
  Shoot freely, pick smartly.
</p>

<p align="center">
  <a href="https://github.com/jamesphotography/SuperPicky/releases/latest"><img src="https://img.shields.io/github/v/release/jamesphotography/SuperPicky?label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey.svg" alt="Platform">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License"></a>
</p>

<p align="center">
  <a href="https://superpicky.app/"><b>Website</b></a> ·
  <a href="https://superpicky.app/videos"><b>16 video tutorials</b></a> ·
  <a href="https://superpicky.app/tutorial/"><b>Guide</b></a> ·
  <a href="https://superpicky.app/faq.html"><b>FAQ</b></a> ·
  <a href="README_zh.md"><b>中文</b></a>
</p>

<!-- 30-second promo, uploaded as a GitHub attachment -->
https://github.com/user-attachments/assets/73b3e07d-5386-488e-88dd-90fecb1f20f1

Back from a trip with 3,000 frames? Point SuperPicky at the folder and press Start. It finds the bird in every photo, checks head sharpness, aesthetics and where your camera's focus point landed, identifies the species in batch, groups bursts, tags flight shots, and hands out 1–3 stars. The results are written into your files, so Lightroom picks them up on import.

About 0.25 s per photo on a MacBook Pro M3 Max, so 3,000 photos take roughly 12 minutes. On Windows, NVIDIA cards get CUDA acceleration.

---

## Features

| | |
|---|---|
| **Smart rating** | Head sharpness + TOPIQ aesthetics → 1–3 stars, plus a Pick flag for the very best. [Video 05](https://superpicky.app/videos#05) |
| **Best of the batch** | Stars are handed out by quota within each batch (3 stars: Beginner 40% / Intermediate 30% / Master 20%, or your own). With Bird ID on, quotas are per species, so rare birds always keep their best frame. |
| **Focus check** | Reads the focus point from your RAW files: critical, good, soft or missed focus. |
| **Batch Bird ID** | BirdTrace engine, 10,000+ species, written into the photo title. Matching is limited to the species of the country and region you choose. [Video 10](https://superpicky.app/videos#10) |
| **Flight & bursts** | SuperFlier tags birds in flight; bursts are grouped so you stop comparing dozens of near-identical frames. [Video 06](https://superpicky.app/videos#06) |
| **Global rarity** | A 0–100 rarity score built on GBIF occurrence data, plus IUCN status, so the rarest birds of the trip rise to the top. [Video 11](https://superpicky.app/videos#11) |
| **Results browser** | Thumbnail grid, filters by stars / species / focus / flight, fullscreen viewer with the AF box, side-by-side compare, batch rating and delete. [Video 08](https://superpicky.app/videos#08) |
| **Filed by species** | Photos are moved into species → rating folders, ready to browse. |
| **Lightroom ready** | Star rating, Pick flag and colour labels go into the file or its XMP sidecar: blue = in flight, green = critical focus, red = focus problems. [Video 07](https://superpicky.app/videos#07) |
| **Labs** | Incremental culling of newly added photos, multi-folder culling, trip reports, eBird export and video analysis. |

New to SuperPicky? The [video tutorials](https://superpicky.app/videos) are 16 short episodes, about two minutes each, in English and Chinese.

---

## Download

Get the latest release from **[superpicky.app](https://superpicky.app/#download)** or **[GitHub Releases](https://github.com/jamesphotography/SuperPicky/releases/latest)**.

| Your computer | Installer |
|---|---|
| Mac with Apple silicon (M1–M5) | macOS · Apple Silicon |
| Intel Mac (before 2020) | macOS · Intel x64 (runs on the CPU) |
| Windows without an NVIDIA card | Windows · x64 · CPU |
| Windows with GTX 10 series or older, GTX 16, RTX 20 / 30 / 40 | Windows · CUDA **CU118** |
| Windows with RTX 50 series | Windows · CUDA **CU130** (3 files: download into one folder, don't rename them, run ①) |

Not sure? RTX 50 → CU130, everything else → CU118. [Video 02](https://superpicky.app/videos#02) walks through it.

Test builds are listed on the [Releases](https://github.com/jamesphotography/SuperPicky/releases) page. For everyday use, stay on the latest release.

---

## How ratings work

Since 4.5.0, stars come from photos competing within the same batch rather than against fixed scores. Sharpness and aesthetics become batch percentiles and are weighted into one ranking; stars are then handed out by quota.

| Stars | Who gets them |
|---|---|
| ★★★ | Top 30% of the batch (or of the species). Beginner 40% / Master 20%. |
| ★★ | The next 30%. Frames where the eye can't be seen, and bursts that already have their share of 3 stars, also land here. |
| ★ | Everything ranked below that. |
| Reject | No bird, or below the basic gates. Excluded from the ranking and from the quota. |

More detail in [video 05](https://superpicky.app/videos#05) and on the [website](https://superpicky.app/#rating).

### Where the results go

- **Metadata**: star rating, Pick flag and colour label, written to the RAW file or an XMP sidecar. Title = species, Description = rating reason, City = head sharpness, State = aesthetics score.
- **Folders**: 3★ → `<Species>/3star_excellent`, 2★ → `<Species>/2star_good`, 1★ → `Other_Birds/1star_average`, no bird / rejects → `Other_Birds/0star_reject`. **Reset** puts everything back.
- **Working data (`.superpicky` folder)**: each processed folder gets a hidden `.superpicky` folder holding `report.db` (scores, ratings, species and bursts, so the results browser can reopen the folder without re-analysing) and `cache` (previews and bird crops, most of the size). Your photos are never stored there. Deleting it doesn't lose your ratings or species names, which are already in the metadata, but the folder would need to be analysed again before you can browse it. To free up space and keep the results, use **Settings → Advanced → Storage → Clear All Preview Caches**, which removes only `cache`.

---

## Run from source

```bash
git clone https://github.com/jamesphotography/SuperPicky.git
cd SuperPicky
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_models.py
python main.py
```

There is also a command-line interface: see the [CLI reference](https://superpicky.app/cli-reference.html).

### Species naming standards (AviList)

SuperPicky supports several English naming standards via the **AviList v2025** mapping. Choose one in **Settings → Common → Bird Name Display Format**:

| Format | Source |
|--------|--------|
| Default | Original species-database names |
| AviList v2025 | AviList unified English names |
| Clements / eBird v2024 | Cornell / eBird taxonomy |
| BirdLife v9 | BirdLife International |
| Scientific name only | Binomial nomenclature |

The mapping is built offline from `scripts_dev/AviList-v2025-11Jun-extended.xlsx`. When a new AviList version is released (usually once a year), replace the xlsx file in `scripts_dev/` and re-run:

```bash
pip install openpyxl  # first time only
python scripts_dev/build_avilist_mapping.py
```

Review unmatched species in the report and add manual overrides to `scripts_dev/avilist_manual_overrides.json` if needed.

---

## Team

| Role | Who |
|---|---|
| Developer | [James Yu](https://github.com/jamesphotography), professional photographer |
| Model training | [Jordan Yu](https://github.com/triple333sR9): SuperEyes · SuperFlier |
| Windows build | Xiaoping · Lyapunov · [Oscar King](https://github.com/OscarKing888) |
| Model porting & optimization | Jianwen Chen |

Thanks to everyone who has [sponsored](https://superpicky.app/sponsors.html) the project, reported bugs or sent in bird photos.

---

## License

Open source under the **GPL-3.0 License**. See [LICENSE](LICENSE).

This project uses:
- **YOLO11** by Ultralytics
- **[BirdTrace FastViT-MA36](https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K)** by Warden: bird species identification, licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
- **OSEA** by Sun Jiao (github.com/sun-jiao/osea): species class list (the identification model itself is no longer bundled)
- **[SuperEyes (SuperBirdEye)](https://github.com/triple333sR9/SuperBirdEye)** and **[SuperFlier](https://github.com/triple333sR9/SuperFlier)** by [Jordan Yu](https://github.com/triple333sR9)
- **TOPIQ** by Chaofeng Chen et al.
- **[ExifTool](https://exiftool.org/)** by Phil Harvey
- **AviList**: AviList Core Team. 2025. AviList: The Global Avian Checklist, v2025. https://doi.org/10.2173/avilist.v2025, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
