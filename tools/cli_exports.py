# -*- coding: utf-8 -*-
"""已处理目录的离线导出入口 / Offline exports from one processed directory."""

from __future__ import annotations

import os
import sqlite3
import sys
from argparse import Namespace
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from core.superpicky_dir import SUPERPICKY_DIRNAME
from tools.report_db import ReportDB


def _load_photos(directory: Path) -> list[dict]:
    """只读载入照片，不初始化或升级数据库 / Read photos without initializing or migrating the DB."""
    db_path = directory / SUPERPICKY_DIRNAME / ReportDB.DB_FILENAME
    if not db_path.is_file():
        raise ValueError(f"No report.db at {db_path}; process the directory first.")
    try:
        # 保留 SQLite 的 WAL 读取能力；immutable 会忽略正在使用的日志。
        # Keep WAL reads enabled; immutable mode is not safe for a live database.
        with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in db.execute("SELECT * FROM photos ORDER BY filename")
            ]
    except sqlite3.Error as exc:
        raise ValueError(f"Cannot read report.db at {db_path}: {exc}") from exc
    if not rows:
        raise ValueError(f"There are no photos in {db_path}.")
    return rows


def _output_path(output: str | Path, suffixes: tuple[str, ...], force: bool) -> Path:
    """校验输出类型和覆盖选项 / Validate the output type and overwrite option."""
    path = Path(output).expanduser().absolute()
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"--output must end with {' or '.join(suffixes)}: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"Output parent directory does not exist: {path.parent}")
    if os.path.lexists(path) and not force:
        raise ValueError(f"Output already exists: {path}; use --force to replace it.")
    return path


def _publish(output: Path, force: bool, write: Callable[[str], None]) -> None:
    """先写临时文件，再发布到目标位置 / Stage a complete file before publishing it."""
    with TemporaryDirectory(prefix=".superpicky-export-", dir=output.parent) as staging:
        temporary = Path(staging) / "export"
        write(str(temporary))
        if force:
            os.replace(temporary, output)
            return
        try:
            # Windows 的 rename 不覆盖目标；POSIX 用 link 避免检查和发布之间的竞态。
            # Windows rename is exclusive; on POSIX link closes the check/publish race.
            if os.name == "nt":
                os.rename(temporary, output)
            else:
                os.link(temporary, output)
        except FileExistsError as exc:
            raise ValueError(
                f"Output already exists: {output}; use --force to replace it."
            ) from exc


def _report_paths(directory: Path, photos: list[dict]) -> None:
    """解析报告生成器使用的路径 / Resolve the paths consumed by the report builder."""
    for row in photos:
        for key in ("current_path", "original_path", "temp_jpeg_path"):
            if row.get(key):
                row[key] = str(directory / row[key])


def export_report(args: Namespace) -> int:
    """把单个目录的照片记录导出为 HTML / Export one folder as HTML (0 success, 1 failure).

    Args: directory, output (.html or .htm), optional force, title and text_only.
    """
    from constants import APP_VERSION
    from core import report_export
    from tools.i18n import get_i18n

    try:
        directory = Path(args.directory).expanduser().absolute()
        force = getattr(args, "force", False)
        output = _output_path(args.output, (".html", ".htm"), force)
        photos = _load_photos(directory)
        _report_paths(directory, photos)
        title = (getattr(args, "title", None) or "").strip() or directory.name
        data = replace(
            report_export.aggregate(photos, include_gps=False), dir_name=title
        )
        encoded: dict[str, str] = {}
        if not getattr(args, "text_only", False):
            for job in report_export.collect_image_jobs(data):
                preview = report_export.encode_preview(
                    job.path, job.max_edge, job.quality
                )
                if preview:
                    encoded[job.job_id] = preview
        html = report_export.build_html(
            data,
            encoded,
            is_zh=get_i18n().current_lang.startswith("zh"),
            app_version=APP_VERSION,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        _publish(
            output,
            force,
            lambda path: report_export.write_report_atomically(path, html),
        )
    except (OSError, ValueError) as exc:
        print(f"Report export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Report saved: {output}")
    return 0


def export_ebird(args: Namespace) -> int:
    """生成本地 eBird CSV，不联网 / Write eBird Record Format locally (0 success, 1 failure).

    Args: directory, output (.csv), location, optional country, force and include_gps.
    Coordinates are omitted unless include_gps is True; nothing is uploaded.
    """
    from core import ebird_export
    from tools.country_names import known_country_codes

    try:
        location = getattr(args, "location", None) or ""
        location = location.translate(str.maketrans("", "", '"“”')).strip()
        if not location or len(location.splitlines()) > 1:
            raise ValueError("--location must be a nonempty, single-line place name.")
        raw_country = getattr(args, "country", None)
        country = "" if raw_country is None else raw_country.strip().upper()
        if raw_country is not None and country not in known_country_codes():
            raise ValueError(
                "--country must be a known two-letter eBird country code (e.g. AU)."
            )
        directory = Path(args.directory).expanduser().absolute()
        force = getattr(args, "force", False)
        output = _output_path(args.output, (".csv",), force)
        photos = _load_photos(directory)
        # 精确点位可能暴露敏感地点；只有调用方明确选择时才写出坐标。
        # Exact coordinates can expose sensitive locations; require explicit opt-in.
        if not getattr(args, "include_gps", False):
            for row in photos:
                row.pop("gps_latitude", None)
                row.pop("gps_longitude", None)
        checklists = ebird_export.build_checklists(photos, location)
        if not checklists:
            raise ValueError(
                "There are no eligible records (2+ stars, species ID, capture time)."
            )
        rows = ebird_export.to_rows(checklists)
        country_column = ebird_export.EBIRD_COLUMNS.index("Country Code")
        for row in rows:
            row[country_column] = country
        _publish(output, force, lambda path: ebird_export.write_csv(path, rows))
    except (OSError, ValueError, TypeError) as exc:
        print(f"eBird export failed: {exc}", file=sys.stderr)
        return 1
    stats = ebird_export.summarize(checklists)
    print(
        f"eBird CSV saved locally: {output} "
        f"({stats['checklists']} checklist(s), {stats['rows']} row(s))."
    )
    needs_review = cast(list[str], stats["needs_review"])
    if needs_review:
        print("Review before importing:", file=sys.stderr)
        for note in needs_review:
            print(f"  {note}", file=sys.stderr)
    return 0
