#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional


ONBOARDING_SCENES = [
    {
        "anchor": "p1-prepare",
        "duration_sec": 6,
        "capture_goal": "建立场景，展示用于首次体验的小型样片目录。",
        "manual_action_cn": "在 Finder 中展示一个干净的样片目录，准备拖入 SuperPicky。",
    },
    {
        "anchor": "p1-launch",
        "duration_sec": 6,
        "capture_goal": "展示应用启动与主界面进入。",
        "manual_action_cn": "启动应用，停留半秒后让主窗口完整出现。",
    },
    {
        "anchor": "p1-main-ui",
        "duration_sec": 7,
        "capture_goal": "快速扫过主界面的关键区域。",
        "manual_action_cn": "依次指向目录输入、摄影水平、功能开关和开始处理按钮。",
    },
    {
        "anchor": "p2-skill",
        "duration_sec": 7,
        "capture_goal": "说明首次使用推荐的摄影水平。",
        "manual_action_cn": "打开摄影水平下拉框，选择“初级”并停顿确认。",
    },
    {
        "anchor": "p2-toggles",
        "duration_sec": 8,
        "capture_goal": "展示首次上手常用的功能组合。",
        "manual_action_cn": "按照片内容切换连拍、飞版和识鸟，最终停在推荐组合上。",
    },
    {
        "anchor": "p2-progress",
        "duration_sec": 8,
        "capture_goal": "展示处理开始后日志在持续刷新。",
        "manual_action_cn": "点击开始处理，录到日志区持续出现新内容为止。",
    },
    {
        "anchor": "p3-ratings",
        "duration_sec": 7,
        "capture_goal": "解释 0 到 3 星的结果分层。",
        "manual_action_cn": "处理完成后，展示统计结果或星级目录结构，强调先看高星。",
    },
    {
        "anchor": "p4-browser",
        "duration_sec": 8,
        "capture_goal": "展示结果浏览器是复查与精选的主战场。",
        "manual_action_cn": "打开结果浏览器，快速滚动并停在高星图片上。",
    },
    {
        "anchor": "p4-compare",
        "duration_sec": 8,
        "capture_goal": "展示难选照片时的全屏或对比查看。",
        "manual_action_cn": "打开全屏或对比查看器，在相近照片之间切换一次。",
    },
    {
        "anchor": "p5-panel",
        "duration_sec": 8,
        "capture_goal": "补充展示识鸟面板与候选结果。",
        "manual_action_cn": "打开识鸟面板，展示候选列表和主结果区域。",
    },
]


def normalize_text(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def has_class(classes: List[str], target: str) -> bool:
    return target in classes


class TutorialParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Dict[str, object]] = []
        self.parts: List[Dict[str, object]] = []
        self.current_part: Optional[Dict[str, object]] = None
        self.current_step: Optional[Dict[str, object]] = None
        self.current_li: Optional[Dict[str, List[str]]] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        class_attr = attrs_dict.get("class", "") or ""
        classes = class_attr.split()
        kind = None

        if tag == "section" and "tutorial-part" in classes:
            kind = "part"
            self.current_part = {
                "id": attrs_dict.get("id", ""),
                "part_no": "",
                "title_cn": "",
                "title_en": "",
                "steps": [],
            }
        elif tag == "div" and "tutorial-step" in classes:
            kind = "step"
            self.current_step = {
                "id": attrs_dict.get("id", ""),
                "reverse": "reverse" in classes,
                "step_kicker": "",
                "sec_no": "",
                "title_cn": "",
                "title_en": "",
                "lead_cn": "",
                "lead_en": "",
                "guide_cn": [],
                "guide_en": [],
            }
        elif tag == "li" and self.current_step and self.has_open_class("step-guide"):
            self.current_li = {"cn": [], "en": []}

        self.stack.append({"tag": tag, "classes": classes, "kind": kind})

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        entry = self.stack.pop()

        if tag == "li" and self.current_li and self.current_step:
            cn_text = normalize_text("".join(self.current_li["cn"]))
            en_text = normalize_text("".join(self.current_li["en"]))
            if cn_text:
                self.current_step["guide_cn"].append(cn_text)
            if en_text:
                self.current_step["guide_en"].append(en_text)
            self.current_li = None

        if entry.get("kind") == "step" and self.current_part and self.current_step:
            for field in ("step_kicker", "sec_no", "title_cn", "title_en", "lead_cn", "lead_en"):
                self.current_step[field] = normalize_text(str(self.current_step[field]))
            self.current_part["steps"].append(self.current_step)
            self.current_step = None
        elif entry.get("kind") == "part" and self.current_part:
            for field in ("part_no", "title_cn", "title_en"):
                self.current_part[field] = normalize_text(str(self.current_part[field]))
            self.parts.append(self.current_part)
            self.current_part = None

    def handle_data(self, data: str) -> None:
        text = normalize_text(data)
        if not text:
            return

        lang = self.current_lang()

        if self.current_step:
            if self.has_open_class("step-kicker"):
                self.current_step["step_kicker"] += f" {text}"
                return
            if self.has_open_class("sec-no") and self.has_open_tag("h3"):
                self.current_step["sec_no"] += f" {text}"
                return
            if self.has_open_tag("h3"):
                target = "title_en" if lang == "en" else "title_cn"
                self.current_step[target] += f" {text}"
                return
            if self.has_open_class("step-lead"):
                target = "lead_en" if lang == "en" else "lead_cn"
                self.current_step[target] += f" {text}"
                return
            if self.current_li is not None and self.has_open_class("step-guide"):
                target = "en" if lang == "en" else "cn"
                self.current_li[target].append(text)
                return

        if self.current_part:
            if self.has_open_class("part-num"):
                self.current_part["part_no"] += f" {text}"
                return
            if self.has_open_tag("h2"):
                target = "title_en" if lang == "en" else "title_cn"
                self.current_part[target] += f" {text}"

    def current_lang(self) -> Optional[str]:
        for entry in reversed(self.stack):
            classes = entry["classes"]
            if has_class(classes, "lang-en"):
                return "en"
            if has_class(classes, "lang-cn"):
                return "cn"
        return None

    def has_open_class(self, class_name: str) -> bool:
        return any(has_class(entry["classes"], class_name) for entry in self.stack)

    def has_open_tag(self, tag_name: str) -> bool:
        return any(entry["tag"] == tag_name for entry in self.stack)


def parse_tutorial(source_path: Path) -> List[Dict[str, object]]:
    parser = TutorialParser()
    parser.feed(source_path.read_text(encoding="utf-8"))
    return parser.parts


def flatten_steps(parts: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    flat: Dict[str, Dict[str, object]] = {}
    for part in parts:
        for step in part["steps"]:
            flat[step["id"]] = {
                "part_id": part["id"],
                "part_no": part["part_no"],
                "part_title_cn": part["title_cn"],
                "part_title_en": part["title_en"],
                **step,
            }
    return flat


def render_shotlist_markdown(parts: List[Dict[str, object]], source_path: Path) -> str:
    lines = [
        "# SuperPicky 4.2.1 教程分镜提要",
        "",
        f"- 来源: `{source_path.as_posix()}`",
        f"- 章节数: {len(parts)}",
        f"- 步骤数: {sum(len(part['steps']) for part in parts)}",
        "",
        "这份文件由 `scripts/generate_onboarding_assets.py` 自动生成，可直接作为录制 checklist 或旁白提词底稿。",
        "",
    ]

    for part in parts:
        lines.append(f"## Part {part['part_no']} · {part['title_cn']}")
        lines.append("")
        for step in part["steps"]:
            lines.append(f"### {step['sec_no']} `{step['id']}` {step['title_cn']}")
            if step["lead_cn"]:
                lines.append(f"- 核心信息: {step['lead_cn']}")
            if step["guide_cn"]:
                lines.append("- 操作步骤:")
                for item in step["guide_cn"]:
                    lines.append(f"  - {item}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_onboarding_manifest(step_map: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    manifest: List[Dict[str, object]] = []
    for index, scene in enumerate(ONBOARDING_SCENES, start=1):
        anchor = scene["anchor"]
        if anchor not in step_map:
            raise KeyError(f"Anchor not found in tutorial: {anchor}")
        step = step_map[anchor]
        manifest.append(
            {
                "shot_id": f"S{index:02d}",
                "filename": f"{index:02d}_{anchor}.mp4",
                "anchor": anchor,
                "duration_sec": scene["duration_sec"],
                "part_no": step["part_no"],
                "sec_no": step["sec_no"],
                "title_cn": step["title_cn"],
                "title_en": step["title_en"],
                "capture_goal": scene["capture_goal"],
                "manual_action_cn": scene["manual_action_cn"],
                "voiceover_cn": step["lead_cn"],
            }
        )
    return manifest


def render_onboarding_markdown(manifest: List[Dict[str, object]]) -> str:
    total_duration = sum(int(item["duration_sec"]) for item in manifest)
    lines = [
        "# SuperPicky Onboarding 视频录制单",
        "",
        f"- 推荐镜头数: {len(manifest)}",
        f"- 目标时长: 约 {total_duration} 秒",
        "- 用法: 按 `shot_id` 顺序逐段录制，失败只需重录当前段。",
        "",
        "| Shot | 锚点 | 时长(s) | 标题 | 录制目标 | 手动动作 |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for item in manifest:
        lines.append(
            f"| {item['shot_id']} | `{item['anchor']}` | {item['duration_sec']} | "
            f"{item['title_cn']} | {item['capture_goal']} | {item['manual_action_cn']} |"
        )

    lines.extend(["", "## 旁白提示", ""])
    for item in manifest:
        lines.append(f"### {item['shot_id']} {item['title_cn']}")
        lines.append(f"- 旁白: {item['voiceover_cn']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate onboarding video assets from tutorial HTML.")
    parser.add_argument(
        "--source",
        default="docs/tutorial-4.2.1.html",
        help="Tutorial HTML source file.",
    )
    parser.add_argument(
        "--out-dir",
        default="docs/Promotion/onboarding",
        help="Output directory for generated onboarding assets.",
    )
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = parse_tutorial(source_path)
    step_map = flatten_steps(parts)
    manifest = build_onboarding_manifest(step_map)

    write_json(out_dir / "tutorial-4.2.1-structure.json", parts)
    (out_dir / "tutorial-4.2.1-shotlist.md").write_text(
        render_shotlist_markdown(parts, source_path),
        encoding="utf-8",
    )
    write_csv(out_dir / "tutorial-4.2.1-onboarding.csv", manifest)
    (out_dir / "tutorial-4.2.1-onboarding.md").write_text(
        render_onboarding_markdown(manifest),
        encoding="utf-8",
    )

    print(f"Generated onboarding assets in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
