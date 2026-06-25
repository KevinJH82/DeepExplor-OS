"""矿种知识库测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mineral_kb import get_mineral_info, list_all_minerals


KNOWN_MINERALS = ["铜", "金", "锂", "铅锌", "钨锡", "稀土", "铁"]


def test_list_all_minerals():
    minerals = list_all_minerals()
    assert len(minerals) >= 8
    for m in KNOWN_MINERALS:
        assert m in minerals, f"缺少矿种: {m}"


def test_copper_info():
    info = get_mineral_info("铜")
    assert info["mineral"] == "铜"
    assert len(info["metallogenic_types"]) > 0
    assert "Cu" in info["all_key_elements"] or "Cu" in str(info["all_key_elements"])
    assert len(info["recommended_data_priority"]) > 0


def test_gold_info():
    info = get_mineral_info("金")
    assert info["mineral"] == "金"
    assert "Au" in info["all_key_elements"]


def test_lithium_info():
    info = get_mineral_info("锂")
    assert info["mineral"] == "锂"
    assert "Li" in info["all_key_elements"]


def test_oil_info():
    info = get_mineral_info("石油")
    assert info["mineral"] == "石油"
    assert "six_elements" in info
    assert len(info["six_elements"]) > 0


def test_alias_normalization():
    # "铜矿" 应自动匹配到 "铜"
    info = get_mineral_info("铜矿")
    assert info["mineral"] == "铜"


def test_unknown_mineral_fallback():
    info = get_mineral_info("未知矿种_xyz")
    assert info is not None
    assert "mineral" in info


def test_all_minerals_have_key_elements():
    for m in KNOWN_MINERALS:
        info = get_mineral_info(m)
        assert len(info["all_key_elements"]) > 0, f"{m} 缺少指示元素"


def test_all_minerals_have_geophysical_methods():
    for m in KNOWN_MINERALS:
        info = get_mineral_info(m)
        assert len(info["all_geophysical_methods"]) > 0, f"{m} 缺少物探方法"
