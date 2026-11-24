"""测试 CLI 的协作暂停及严格续跑 / Cooperative CLI pause and strict resume tests."""

import signal
from unittest.mock import Mock

import pytest

from advanced_config import get_advanced_config
from core.photo_processor import PhotoProcessor, ProcessingSettings
from tools.cli_processor import CLIProcessingPaused, CLIProcessor, CLIResumeError
from tools.resume_state import ResumeStateManager


@pytest.fixture
def cli_processor(tmp_path):
    """使用真实核心流程，但不写照片元数据 / Use the real pipeline without metadata writes."""
    get_advanced_config().set_metadata_write_mode("none")
    return CLIProcessor(str(tmp_path), verbose=False,
                        settings=ProcessingSettings(detect_burst=False))


def test_ctrl_c_preserves_pending_and_resume_does_not_replay(tmp_path, cli_processor, monkeypatch):
    for stem in ("01", "02", "03"):
        (tmp_path / f"{stem}.jpg").touch()
    state = ResumeStateManager(str(tmp_path))
    original_handler = signal.getsignal(signal.SIGINT)
    summary = Mock()
    monkeypatch.setattr(cli_processor, "_print_summary", summary)
    seen = []

    def pause_first_run(self, files, _raw_dict, **_display):
        seen.append(files[0])
        assert self.resume_state.mark_completed(self._resume_prefix(files[0]))
        signal.raise_signal(signal.SIGINT)
        self._check_cancelled()

    monkeypatch.setattr(PhotoProcessor, "_process_images", pause_first_run)
    with pytest.raises(CLIProcessingPaused) as paused:
        cli_processor.process(organize_files=False, cleanup_temp=False)
    assert paused.value.checkpoint_available
    assert signal.getsignal(signal.SIGINT) is original_handler
    assert state.load()["pending_prefixes"] == ["02", "03"]
    summary.assert_not_called()
    (tmp_path / "04.jpg").touch()

    def finish_resume(self, files, _raw_dict, *, display_start, display_total):
        assert (display_start, display_total) == (2, 3)
        assert not self._should_stop()
        seen.extend(files)
        self.stats["total"] = len(files)

    monkeypatch.setattr(PhotoProcessor, "_process_images", finish_resume)
    assert cli_processor.process(organize_files=False, cleanup_temp=False, resume=True)["total"] == 2
    assert seen == ["01.jpg", "02.jpg", "03.jpg"]
    assert not state.exists()
    assert signal.getsignal(signal.SIGINT) is original_handler
    summary.assert_called_once()


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (None, "missing"),
        ("{bad json", "invalid"),
        ('{"version":1,"status":"running","total_files":1,"pending_prefixes":[{}]}',
         "invalid"),
        ('{"version":1,"status":"running","total_files":1,"pending_prefixes":["gone"]}',
         "no_pending"),
        ('{"version":1,"status":"running","total_files":1,"pending_prefixes":[]}',
         "no_pending"),
    ],
)
def test_resume_refuses_unusable_checkpoint_without_writes(
    tmp_path, cli_processor, monkeypatch, contents, reason
):
    existing_photo = tmp_path / "new.jpg"
    existing_photo.write_bytes(b"unchanged")
    state = tmp_path / ".superpicky" / "resume_state.json"
    if contents is not None:
        state.parent.mkdir()
        state.write_text(contents, encoding="utf-8")
    core_process = Mock(side_effect=AssertionError("must not start the write pipeline"))
    monkeypatch.setattr(cli_processor.processor, "process", core_process)
    before = set(tmp_path.rglob("*"))

    with pytest.raises(CLIResumeError) as error:
        cli_processor.process(resume=True)

    assert error.value.reason == reason
    assert set(tmp_path.rglob("*")) == before
    assert existing_photo.read_bytes() == b"unchanged"
    if contents is not None:
        assert state.read_text(encoding="utf-8") == contents
    core_process.assert_not_called()


@pytest.mark.parametrize("older_checkpoint", [False, True])
def test_ctrl_c_during_raw_extraction(tmp_path, cli_processor, monkeypatch, older_checkpoint):
    (tmp_path / "01.NEF").touch()
    state = ResumeStateManager(str(tmp_path))
    if older_checkpoint:
        assert state.start(["older"])
    original_handler = signal.getsignal(signal.SIGINT)

    def interrupt_raw_extraction(self, _raws, _files):
        signal.raise_signal(signal.SIGINT)
        self._check_cancelled()

    monkeypatch.setattr(PhotoProcessor, "_convert_raws", interrupt_raw_extraction)
    with pytest.raises(CLIProcessingPaused) as paused:
        cli_processor.process()
    assert paused.value.checkpoint_available is not older_checkpoint
    assert state.load()["pending_prefixes"] == (["older"] if older_checkpoint else ["01"])
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_resume_does_not_fall_back_to_fresh_after_unreadable_raw(
    tmp_path, cli_processor, monkeypatch
):
    (tmp_path / "01.NEF").touch()
    (tmp_path / "new.jpg").touch()
    state = ResumeStateManager(str(tmp_path))
    assert state.start(["01"])
    monkeypatch.setattr(PhotoProcessor, "_convert_raws", lambda *_: None)
    process_images = Mock(side_effect=AssertionError("must not reprocess fresh photos"))
    monkeypatch.setattr(PhotoProcessor, "_process_images", process_images)

    with pytest.raises(CLIResumeError) as error:
        cli_processor.process(resume=True)
    assert error.value.reason == "no_pending"
    assert state.load()["pending_prefixes"] == ["01"]
    process_images.assert_not_called()
