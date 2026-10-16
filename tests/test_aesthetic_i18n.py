"""
鸟种颜值 UI i18n 键存在性单测。
Species-beauty UI i18n key presence tests.
"""
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("locale", ["locales/zh_CN.json", "locales/en_US.json"])
@pytest.mark.parametrize("key", ["meta_species_beauty", "sort_species_beauty"])
def test_browser_keys_exist(locale, key):
    data = json.loads(Path(locale).read_text(encoding="utf-8"))
    assert key in data["browser"]
    assert data["browser"][key].strip() != ""
