# -*- coding: utf-8 -*-
"""
回归测试：「其他鸟类」目录名必须跟随界面语言。

首次处理（photo_processor）显式传入 logs.folder_other_birds，英文界面建的是
Other_Birds、繁体界面建的是「其他鳥類」；而处理后在结果浏览器里改星级 / 改鸟种
（rating_mover）不传这个参数，落到 compute_target_folder 的硬编码默认值
「其他鸟类」——于是低星照片被移进一个新建的简体中文目录，与原目录分家。

Regression: the "Other Birds" folder name must follow the UI language.
Processing passes logs.folder_other_birds explicitly, but post-processing
moves in rating_mover relied on the hard-coded Simplified Chinese default,
splitting low-star photos into a second, Chinese-named folder.
"""

import os

import pytest

from tools.i18n import get_i18n

# (界面语言, 期望的其他鸟类目录名, 期望的 1 星目录名)
# (UI language, expected Other Birds folder, expected 1-star folder)
CASES = [
    ("en_US", "Other_Birds", "1star_average"),
    ("zh_TW", "其他鳥類", "1星_普通"),
    ("zh_CN", "其他鸟类", "1星_普通"),
]


@pytest.fixture
def i18n():
    """
    切换界面语言的夹具，测试结束后恢复原语言。

    Fixture yielding the I18n singleton; restores the original language after.
    """
    inst = get_i18n()
    original = inst.current_lang
    yield inst
    inst.switch_language(original)


def _touch(path: str) -> str:
    """建空文件（含父目录）/ Create an empty file with parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


@pytest.mark.parametrize("lang,other,one_star", CASES)
def test_default_label_follows_ui_language(i18n, lang, other, one_star):
    """不传 other_birds_label 时用当前界面语言的名称。/ Default follows UI language."""
    from core.folder_layout import compute_target_folder
    i18n.switch_language(lang)
    assert compute_target_folder(1, "Bald Eagle", "species-first") == os.path.join(other, one_star)
    # 显式传入仍然优先 / An explicit label still wins
    assert compute_target_folder(1, None, "species-first", "X") == os.path.join("X", one_star)


@pytest.mark.parametrize("lang,other,one_star", CASES)
def test_rating_drop_moves_into_ui_language_other_birds(tmp_path, i18n, lang, other, one_star):
    """
    改星级降到 1 星：移进当前语言的「其他鸟类」目录，与处理时建的目录合并。

    Dropping to 1 star moves into the UI-language Other Birds folder — the
    same folder processing created — instead of a new Chinese one.
    """
    from constants import get_rating_folder_name
    from core.rating_mover import move_photo_on_metadata_change

    i18n.switch_language(lang)
    root = str(tmp_path)
    src = _touch(os.path.join(root, "Bald_Eagle", get_rating_folder_name(3), "IMG_1.CR3"))
    # 处理时已建好的同语言「其他鸟类」目录 / folder created during processing
    os.makedirs(os.path.join(root, other, one_star))

    photo = {"filename": "IMG_1", "current_path": src,
             "bird_species_cn": "白头海雕", "bird_species_en": "Bald Eagle"}
    moved = move_photo_on_metadata_change(
        root, photo, 1, "Bald_Eagle", "species-first", None, "IMG_1")

    assert moved
    assert os.path.exists(os.path.join(root, other, one_star, "IMG_1.CR3"))
    stray = [n for n in ("其他鸟类", "其他鳥類", "Other_Birds")
             if n != other and os.path.exists(os.path.join(root, n))]
    assert stray == [], f"stray Other Birds folder created: {stray}"
