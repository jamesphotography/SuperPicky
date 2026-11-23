"""Command-line argument and dispatch contracts / 命令行解析与分发约定。"""

import sys
from types import SimpleNamespace

import pytest

import superpicky_cli


@pytest.mark.parametrize(
    ("module", "handler", "command", "extra", "expected"),
    [
        ("tools.cli_exports", "export_report", "report", ["-o", "out.html"],
         {"output": "out.html", "force": False, "text_only": False}),
        ("tools.cli_exports", "export_ebird", "export-ebird",
         ["--output", "out.csv", "--location", "湖边", "--country", "GB"],
         {"output": "out.csv", "force": False, "location": "湖边", "country": "GB"}),
        ("tools.cli_video", "analyze_video", "video", ["--no-species", "--max-frames", "20"],
         {"organize": False, "species": False, "max_frames": 20, "flight": None}),
    ],
)
def test_optional_command_dispatch(tmp_path, monkeypatch, module, handler, command, extra, expected):
    def run(args):
        assert vars(args).items() >= expected.items()
        assert getattr(args, "path" if command == "video" else "directory") == str(tmp_path)
        return 17

    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{handler: run}))
    monkeypatch.setattr(sys, "argv", ["superpicky_cli", command, str(tmp_path), *extra])
    assert superpicky_cli.main() == 17


def test_resume_flag_is_passed_to_photo_command(tmp_path, monkeypatch):
    def run(args):
        assert args.resume is True
        assert args.directory == str(tmp_path)
        assert not args.organize
        return 130

    monkeypatch.setattr(superpicky_cli, "cmd_process", run)
    monkeypatch.setattr(sys, "argv", ["superpicky_cli", "process", str(tmp_path),
                                      "--resume", "--no-organize"])
    assert superpicky_cli.main() == 130
