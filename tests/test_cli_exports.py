# -*- coding: utf-8 -*-
import csv
import os
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest
from PIL import Image

from core import report_export
from tools.cli_exports import export_ebird, export_report
from tools.report_db import ReportDB


@pytest.fixture
def processed(tmp_path):
    directory = tmp_path / "观鸟 #1?" if os.name != "nt" else tmp_path / "观鸟 #1"
    preview = directory / ".superpicky" / "cache" / "preview.jpg"
    preview.parent.mkdir(parents=True)
    Image.new("RGB", (48, 32), "goldenrod").save(preview)
    photo = {
        "filename": "DSC0001.NEF",
        "original_path": "DSC0001.NEF",
        "temp_jpeg_path": str(preview.relative_to(directory)),
        "has_bird": 1,
        "rating": 3,
        "bird_species_cn": "彩虹蜂虎",
        "bird_species_en": "Rainbow Bee-eater",
        "date_time_original": "2026:09:04 07:30:00",
        "gps_latitude": -34.12345,
        "gps_longitude": 138.12345,
    }
    with ReportDB(str(directory)) as db:
        db.insert_photo(photo)
        db.insert_photo(
            dict(
                photo,
                filename="DSC0002.NEF",
                rating=2,
                date_time_original="2026:09:04 08:30:00",
            )
        )
        db.insert_photo(
            dict(
                photo,
                filename="DSC0003.NEF",
                rating=1,
                bird_species_cn=None,
                bird_species_en="Not exported",
            )
        )
    return directory


def _read_csv(output):
    with output.open(encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


def test_report_embeds_existing_preview_and_escapes_title(processed, tmp_path, capsys):
    output = tmp_path / "reports" / "bird.html"
    output.parent.mkdir()
    assert (
        export_report(
            Namespace(directory=processed, output=output, title="观鸟 <2026> & friends")
        )
        == 0
    )
    html = output.read_text(encoding="utf-8")
    assert "观鸟 &lt;2026&gt; &amp; friends" in html
    assert "Rainbow Bee-eater" in html
    assert "data:image/jpeg;base64," in html
    assert str(processed) not in html
    assert "-34.12345" not in html
    assert str(output) in capsys.readouterr().out


def test_export_does_not_create_missing_output_parent(processed, tmp_path, capsys):
    output = tmp_path / "missing" / "report.html"
    assert export_report(Namespace(directory=processed, output=output)) == 1
    assert "Output parent directory does not exist" in capsys.readouterr().err
    assert not output.parent.exists()


def test_report_text_only_does_not_encode_previews(processed, tmp_path, monkeypatch):
    def unexpected_encode(*args):
        pytest.fail("Text-only export must not decode previews")

    monkeypatch.setattr(report_export, "encode_preview", unexpected_encode)
    output = tmp_path / "text.html"
    assert (
        export_report(Namespace(directory=processed, output=output, text_only=True))
        == 0
    )
    html = output.read_text(encoding="utf-8")
    assert processed.name in html
    assert "Rainbow Bee-eater" in html
    assert "data:image/jpeg;base64," not in html


def test_report_missing_preview_still_produces_html(processed, tmp_path):
    (processed / ".superpicky" / "cache" / "preview.jpg").unlink()
    output = tmp_path / "missing-preview.html"
    assert export_report(Namespace(directory=processed, output=output)) == 0
    html = output.read_text(encoding="utf-8")
    assert 'class="ph ' in html
    assert "data:image/jpeg;base64," not in html


def test_ebird_outputs_record_format_without_gps(processed, tmp_path, capsys):
    output = tmp_path / "observations.csv"
    args = Namespace(
        directory=processed, output=output, location="  观鸟公园  ", country="au"
    )
    assert export_ebird(args) == 0
    (row,) = _read_csv(output)
    assert len(row) == 19
    assert row[:4] == ["", "Merops", "ornatus", "1"]
    assert row[5:13] == ["观鸟公园", "", "", "9/4/2026", "7:30", "", "AU", "Incidental"]
    assert row[15] == "N"
    assert "1 checklist" in capsys.readouterr().out


def test_ebird_gps_is_explicit_opt_in(processed, tmp_path):
    output = tmp_path / "observations.csv"
    args = Namespace(
        directory=processed, output=output, location="Park", include_gps=True
    )
    assert export_ebird(args) == 0
    (row,) = _read_csv(output)
    assert [float(value) for value in row[6:8]] == pytest.approx(
        [-34.12345, 138.12345], abs=0.001
    )
    assert row[11] == ""


def test_ebird_prompts_review_for_unmapped_species(processed, tmp_path, capsys):
    with ReportDB(str(processed)) as db:
        db.update_photo("DSC0003.NEF", {"rating": 2})
    output = tmp_path / "review.csv"
    assert (
        export_ebird(Namespace(directory=processed, output=output, location="Park"))
        == 0
    )
    assert any(row[0] == "Not exported" for row in _read_csv(output))
    assert "Not exported - please verify" in capsys.readouterr().err


@pytest.mark.parametrize(
    "kwargs, flag",
    [
        ({"location": "  "}, "--location"),
        ({"location": '" ”'}, "--location"),
        ({"location": "Park\nLake"}, "--location"),
        ({"country": "ZZ"}, "--country"),
    ],
)
def test_ebird_rejects_invalid_location_or_country(
    processed, tmp_path, capsys, kwargs, flag
):
    output = tmp_path / "observations.csv"
    args = Namespace(
        **{"directory": processed, "output": output, "location": "Park", **kwargs}
    )
    assert export_ebird(args) == 1
    assert flag in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "state, error",
    [("missing", "report.db"), ("empty", "no photos"), ("invalid", "report.db")],
)
@pytest.mark.parametrize(
    "runner, suffix", [(export_report, ".html"), (export_ebird, ".csv")]
)
def test_missing_empty_or_invalid_db_creates_no_output(
    tmp_path, capsys, state, error, runner, suffix
):
    directory = tmp_path / "photos"
    directory.mkdir()
    db_path = directory / ".superpicky" / "report.db"
    if state == "empty":
        with ReportDB(str(directory)):
            pass
    elif state == "invalid":
        db_path.parent.mkdir()
        db_path.write_text("not SQLite", encoding="utf-8")
    output = tmp_path / ("export" + suffix)
    assert runner(Namespace(directory=directory, output=output, location="Park")) == 1
    assert error in capsys.readouterr().err
    assert not output.exists()
    if state == "missing":
        assert not db_path.parent.exists()


def test_ebird_no_eligible_records_does_not_overwrite(processed, tmp_path, capsys):
    with ReportDB(str(processed)) as db:
        for row in db.get_all_photos():
            db.update_photo(row["filename"], {"rating": 1})
    output = tmp_path / "observations.csv"
    output.write_text("existing", encoding="utf-8")
    assert (
        export_ebird(
            Namespace(directory=processed, output=output, location="Park", force=True)
        )
        == 1
    )
    assert "no eligible" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize(
    "runner, suffix", [(export_report, ".html"), (export_ebird, ".csv")]
)
def test_overwrite_requires_force(processed, tmp_path, capsys, runner, suffix):
    output = tmp_path / ("export" + suffix)
    output.write_text("existing", encoding="utf-8")
    args = Namespace(
        directory=processed, output=output, location="Park", text_only=True
    )
    assert runner(args) == 1
    assert "--force" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "existing"
    args.force = True
    assert runner(args) == 0
    assert output.read_text(encoding="utf-8") != "existing"


def test_concurrent_output_creation_does_not_clobber(processed, tmp_path, monkeypatch):
    output = tmp_path / "report.html"
    original = report_export.write_report_atomically

    def write_and_race(path, html):
        original(path, html)
        output.write_text("winner", encoding="utf-8")

    monkeypatch.setattr(report_export, "write_report_atomically", write_and_race)
    assert (
        export_report(Namespace(directory=processed, output=output, text_only=True))
        == 1
    )
    assert output.read_text(encoding="utf-8") == "winner"
    assert set(tmp_path.iterdir()) == {processed, output}


def test_write_failure_keeps_previous_output(processed, tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.html"
    output.write_text("previous report", encoding="utf-8")

    def interrupted_write(path, html):
        Path(path).write_text("partial report", encoding="utf-8")
        raise OSError("simulated disk error")

    monkeypatch.setattr(report_export, "write_report_atomically", interrupted_write)
    args = Namespace(directory=processed, output=output, text_only=True, force=True)
    assert export_report(args) == 1
    assert "simulated disk error" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "previous report"
    assert set(tmp_path.iterdir()) == {processed, output}


def test_export_does_not_migrate_or_repair_db(processed, tmp_path):
    db_path = processed / ".superpicky" / "report.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM meta WHERE key = 'schema_version'")
        connection.execute("UPDATE photos SET current_path = temp_jpeg_path")
    connection.close()
    before = db_path.read_bytes()
    output = tmp_path / "existing.html"
    assert export_report(Namespace(directory=processed, output=output)) == 0
    assert db_path.read_bytes() == before
    assert (
        export_report(Namespace(directory=processed, output=db_path, force=True)) == 1
    )
    assert db_path.read_bytes() == before


def test_export_can_read_live_wal(processed, tmp_path):
    output = tmp_path / "live.html"
    with ReportDB(str(processed)) as db:
        db.update_photo(
            "DSC0001.NEF",
            {"bird_species_cn": None, "bird_species_en": "WAL-only species"},
        )
        assert (
            export_report(Namespace(directory=processed, output=output, text_only=True))
            == 0
        )
    assert "WAL-only species" in output.read_text(encoding="utf-8")
