"""
geo-analyser 蚀变接入(A 旁证重排 + B 地表项升级)回归测试。

覆盖:
- geo_bridge_common:北上 transform、重投影不翻转、括号 normalize、ROI 重叠。
- alteration_bridge:anomaly_type 子串映射;油气端到端(匹配/B升级/A叠层);
  金属稀疏门控;**A 不修改 Au_deep** 的铁律。
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Python_Project" / "web_app"))

GA_ROOT = "/opt/deepexplor-services/geo-analyser/results"
QY = os.path.join(GA_ROOT, "甘肃庆阳华池县油气6个钻井验证_油气_-92_47km2_20260309任务_20260311下载",
                  "常规油藏_微渗漏蚀变模式_20260602-092330")
BX = os.path.join(GA_ROOT, "辽宁本溪市铜钼矿", "斑岩型铜钼矿_20260602-132610")


def _cfg(**kw):
    base = {'enabled': True, 'results_root': GA_ROOT, 'mode_A_rerank': True,
            'mode_B_surface': True, 'min_roi_overlap': 0.15, 'consistency_weight': 0.25,
            'min_mineral_coverage': 0.30, 'min_run_pixels': 2000,
            'min_high_conf_frac': 0.001, 'explicit_run_id': None}
    base.update(kw)
    return base


def _grid_from_manifest(man_path, H, W):
    import json
    man = json.load(open(man_path, encoding='utf-8'))
    c = np.array(man['roi_geojson']['coordinates'][0])
    lonG, latG = np.meshgrid(np.linspace(c[:, 0].min(), c[:, 0].max(), W),
                             np.linspace(c[:, 1].min(), c[:, 1].max(), H))
    return lonG, latG, np.ones((H, W), bool), c


class TestProxyMapping:
    def test_anomaly_type_substring_rules(self):
        from utils.alteration_bridge import _proxies_for
        assert _proxies_for('Al-OH 高岭石/伊利石', '粘土矿化(微渗漏诱发)') == {'Hydroxy_anomaly'}
        assert _proxies_for('烃类胁迫导致叶绿素↓', '植被红边胁迫(NDRE)') == {'NDVI_inv'}
        assert _proxies_for('Fe³⁺ → Fe²⁺ 还原褪色', '红层褪色异常') == {'Ferric', 'Fe_anomaly'}
        assert _proxies_for('Mg-OH', '绿泥石') == {'Clay'}
        assert _proxies_for('TIR-K', '钾长石') == set()  # 无对应代理 → 跳过


class TestBridgeCommon:
    def test_transform_north_up(self):
        pytest.importorskip("rasterio")
        from utils.geo_bridge_common import build_dst_transform, normalize_deposit_key
        lonG, latG = np.meshgrid(np.linspace(100, 101, 20), np.linspace(30, 31, 16))
        tr, crs = build_dst_transform(lonG, latG, (16, 20))
        assert tr.e < 0                       # 负 py → row0=北
        assert abs(tr.f - 31.0) < 1e-9        # row0 = lat_max
        assert normalize_deposit_key('BIF型铁矿（条带状铁建造）') == 'BIF型铁矿(条带状铁建造)'


@pytest.mark.skipif(not os.path.isdir(QY), reason="geo-analyser 庆阳 run 不可用")
class TestAlterationOilGas:
    def setup_method(self):
        pytest.importorskip("rasterio")
        pytest.importorskip("shapely")

    def test_end_to_end_oil_gas(self):
        from utils.alteration_bridge import (load_alteration_for_run, apply_surface_upgrade,
            compute_consistency_overlay, rerank_top_anomalies, PROXY_KEYS)
        H, W = 60, 66
        lonG, latG, inROI, poly = _grid_from_manifest(os.path.join(QY, "manifest.json"), H, W)
        layers = load_alteration_for_run('petroleum', lonG, latG, inROI, poly, _cfg())
        assert layers is not None and '庆阳' in layers.run_id and not layers.weak
        assert layers.composite_score is not None
        assert 'NDVI_inv' in layers.proxies and 'Hydroxy_anomaly' in layers.proxies

        # B:升级
        rng = np.random.RandomState(0)
        proxies = {k: rng.rand(H, W) for k in PROXY_KEYS}
        orig = proxies['NDVI_inv'].copy()
        newp, report = apply_surface_upgrade(proxies, layers, 'petroleum', inROI)
        assert not np.array_equal(newp['NDVI_inv'], orig)
        assert 'altered' in report['NDVI_inv']

        # A:Au_deep 绝不被修改
        Au = np.clip(rng.rand(H, W), 0, 1)
        Au_copy = Au.copy()
        ov = compute_consistency_overlay(Au, layers, inROI)
        assert ov is not None
        assert np.array_equal(Au, Au_copy), "A 绝不能修改 Au_deep"
        assert not np.allclose(ov['rerank_score'], Au)
        b = rerank_top_anomalies(Au, inROI, lonG, latG, 20)
        r = rerank_top_anomalies(ov['rerank_score'], inROI, lonG, latG, 20)
        assert len(b['lonTop']) == 20 and len(r['lonTop']) == 20


@pytest.mark.skipif(not os.path.isdir(BX), reason="geo-analyser 本溪 run 不可用")
class TestAlterationMetalGate:
    def test_copper_weak_gated_but_b_works(self):
        pytest.importorskip("rasterio")
        pytest.importorskip("shapely")
        from utils.alteration_bridge import load_alteration_for_run
        lonG, latG, inROI, poly = _grid_from_manifest(os.path.join(BX, "manifest.json"), 33, 34)
        layers = load_alteration_for_run('copper', lonG, latG, inROI, poly, _cfg())
        assert layers is not None
        assert layers.weak is True                      # 1053px<2000 → A 不重排
        assert 'Hydroxy_anomaly' in layers.proxies      # 绢云母 Al-OH → B 仍升级
