# -*- coding: utf-8 -*-
"""
浏览器改星级：必须先移动文件、再按移动后的新路径写元数据。

2026-10-20 实拍批次：手动升到 4★ 的 5 张进了「4星_精华」目录，但 Lightroom 读到的
仍是 3★。原因是写 EXIF 与移动文件是两个并发线程——移动先把 RAW 和 .xmp 边车搬走，
exiftool 还在写旧目录，搬走的边车于是留着 3★，旧目录只剩 *.xmp_exiftool_tmp 或孤儿 .xmp。

Rating change in the browser must move first, then write metadata at the new path.
The write and the move used to race in two threads, leaving the moved sidecar at 3★.
"""
import os
import threading
import time

import ui.results_browser_window as rbw


def test_metadata_written_at_moved_path_in_order(tmp_path, monkeypatch):
    old_dir = tmp_path / "怯地刺莺" / "3星_优选"
    old_dir.mkdir(parents=True)
    raw = old_dir / "_Z9W5655.NEF"
    raw.write_bytes(b"raw")
    photo = {"filename": "_Z9W5655", "current_path": str(raw), "bird_species_cn": "怯地刺莺"}
    events = []

    def fake_move(dir_path, ph, new_rating, bird_name, layout, report_db, db_key):
        # 模拟 core.rating_mover：搬进新星级目录并更新 current_path（故意放慢，暴露并发）
        time.sleep(0.1)
        new_dir = tmp_path / "怯地刺莺" / f"{new_rating}星"
        new_dir.mkdir(parents=True, exist_ok=True)
        dest = new_dir / os.path.basename(ph["current_path"])
        os.replace(ph["current_path"], dest)
        ph["current_path"] = str(dest)
        events.append(("move", new_rating))
        return True

    class FakeWriter:
        def set_rating_and_pick(self, path, rating, caption=None):
            events.append(("write", rating, path, os.path.exists(path), caption))
            return True

    import core.rating_mover
    import tools.exiftool_manager
    monkeypatch.setattr(core.rating_mover, "move_photo_on_metadata_change", fake_move)
    monkeypatch.setattr(tools.exiftool_manager, "get_exiftool_manager", lambda: FakeWriter())
    monkeypatch.setattr(rbw, "_photo_bird_name", lambda ph, i18n: "怯地刺莺")

    # 记下后台线程以便 join / record the background threads so the test can join them
    started = []

    class RecordingThread(threading.Thread):
        def start(self):
            started.append(self)
            super().start()

    monkeypatch.setattr(rbw.threading, "Thread", RecordingThread)

    # 连续两次：先 4★ 再 5★ / two quick changes: 4★ then 5★
    rbw._trigger_rating_move(str(tmp_path), photo, 4, None, None, "_Z9W5655", caption="cap4")
    rbw._trigger_rating_move(str(tmp_path), photo, 5, None, None, "_Z9W5655", caption="cap5")
    for t in started:
        t.join(timeout=5)
    assert len(started) == 2 and not any(t.is_alive() for t in started)

    assert [e[0] for e in events] == ["move", "write", "move", "write"]
    _, r4, p4, ok4, c4 = events[1]
    _, r5, p5, ok5, c5 = events[3]
    assert (r4, c4, ok4) == (4, "cap4", True) and p4.endswith(os.path.join("4星", "_Z9W5655.NEF"))
    assert (r5, c5, ok5) == (5, "cap5", True) and p5.endswith(os.path.join("5星", "_Z9W5655.NEF"))
