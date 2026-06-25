"""
舒曼波共振遥感矿产预测系统 - 核心分析引擎
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import json
from scipy.ndimage import gaussian_filter
from utils.geo_utils import (read_sentinel2, read_landsat8, read_aster, read_dem_and_roi,
                              get_band, fill_aster_nan, mat2gray_roi, get_yakymchuk_params,
                              get_mineral_thresholds)


class FusionEngine:
    """融合引擎"""

    def __init__(self, detectors: Dict):
        """
        初始化融合引擎

        Args:
            detectors: 探测器字典
        """
        self.detectors = detectors
        self.results = {}

    def compute_all(self, context):
        """
        执行所有探测器计算

        Args:
            context: 数据上下文
        """
        from .detectors import RedEdgeDetector, IntrinsicDetector, SlowVarsDetector

        # 创建探测器实例
        if 'red_edge' in self.detectors:
            self.detectors['red_edge'] = RedEdgeDetector()
        if 'intrinsic' in self.detectors:
            self.detectors['intrinsic'] = IntrinsicDetector()
        if 'slow_vars' in self.detectors:
            self.detectors['slow_vars'] = SlowVarsDetector()

        # 执行计算
        for name, detector in self.detectors.items():
            if name != 'known_anomaly':  # known_anomaly 暂不实现
                print(f"  [FusionEngine] 计算 {name}...")
                result = detector.calculate(context)
                self.results[name] = result

    def get_fused_mask(self, detectors_list: List[str]) -> np.ndarray:
        """
        融合探测器的掩码

        Args:
            detectors_list: 探测器名称列表

        Returns:
            融合后的掩码
        """
        if not detectors_list:
            return np.zeros((100, 100))

        # 获取第一个探测器的掩码作为基础
        first_detector = detectors_list[0]
        if first_detector in self.results:
            fused = self.results[first_detector].mask.copy()
        else:
            return np.zeros((100, 100))

        # 融合其他探测器的掩码
        for detector_name in detectors_list[1:]:
            if detector_name in self.results:
                detector_mask = self.results[detector_name].mask
                # 使用最大值融合
                fused = np.maximum(fused, detector_mask)

        return fused


class MineralEngine:
    """矿产预测分析引擎"""

    def __init__(self):
        """初始化分析引擎"""
        self.logs = []

    def log(self, message: str, level: str = 'INFO'):
        """添加日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] [{level}] {message}"
        self.logs.append(log_entry)
        print(log_entry)

    def run_analysis(self, config: Dict) -> Tuple[List[str], Dict]:
        """
        运行完整分析流程

        Args:
            config: 分析配置字典

        Returns:
            Tuple[logs, results]: 日志列表和结果字典
        """
        self.logs = []
        self.log("=== 开始新的分析任务 ===")

        try:
            # 1. 加载数据
            self.log("正在初始化数据上下文...")
            data_context = self._load_data(config)

            # 2. 初始化融合引擎
            self.log("初始化融合引擎...")
            fusion_engine = self._init_fusion_engine(config)

            # 3. 执行计算
            self.log("开始计算各异常层...")
            fusion_engine.compute_all(data_context)

            # 4. 融合结果
            detectors_list = config.get('detectors', [])
            final_mask = fusion_engine.get_fused_mask(detectors_list)

            # 5. 后处理
            self.log("执行结果融合与后处理...")
            results = self._post_process(data_context, fusion_engine, final_mask, config)

            # 6. 生成可视化
            self.log("生成可视化结果...")
            self._generate_visualizations(results, config, data_context, fusion_engine)

            self.log("✅ 所有流程完成！")

            return self.logs, results

        except Exception as e:
            error_msg = f"分析失败: {str(e)}"
            self.log(error_msg, 'ERROR')
            raise

    def _load_data(self, config: Dict) -> Dict:
        """加载输入数据"""
        self.log("正在加载数据...")

        data_dir = config['data_dir']
        roi_file = config['roi_file']

        self.log(f"ROI文件路径: {roi_file}")
        self.log(f"数据目录: {data_dir}")

        # 处理ROI文件路径
        if not os.path.exists(roi_file):
            possible_paths = [
                roi_file,
                os.path.abspath(roi_file),
                os.path.join(os.getcwd(), roi_file),
                os.path.join(os.path.dirname(__file__), '..', roi_file)
            ]

            for path in possible_paths:
                self.log(f"尝试路径: {path}")
                if os.path.exists(path):
                    roi_file = path
                    self.log(f"找到有效路径: {roi_file}")
                    break
            else:
                raise ValueError(f"ROI文件不存在: {roi_file}")

        # 使用智能列检测加载 ROI 文件
        try:
            roi_data = self._read_roi_robust(roi_file)
            self.log(f"ROI文件读取成功，检测到 {len(roi_data['lon_roi'])} 个坐标点")
        except Exception as e:
            self.log(f"读取ROI文件失败: {str(e)}", 'ERROR')
            raise ValueError(f"无法读取ROI文件 {roi_file}: {str(e)}")

        # 未上传数据目录时,按 ROI 从交付库自动匹配并定位季节数据目录(上传 zip 则优先用上传)
        if not data_dir or not os.path.isdir(data_dir):
            dcfg = config.get('delivery', {}) or {}
            if dcfg.get('enabled', True):
                try:
                    from utils.delivery import resolve_delivery_data_dir
                    roi_poly = np.column_stack([roi_data['lon_roi'], roi_data['lat_roi']])
                    resolved, info = resolve_delivery_data_dir(roi_poly, dcfg, log=self.log)
                    if resolved:
                        data_dir = resolved
                        self.log(f"交付库自动取数: 项目={info['project']} "
                                 f"ROI重叠={info['overlap']:.2f} 季节={info['season']} -> {data_dir}")
                except Exception as e:
                    self.log(f"交付库自动取数异常: {e}", 'WARN')
            if not data_dir or not os.path.isdir(data_dir):
                raise ValueError("未上传数据目录,且交付库中未找到 ROI 匹配的项目(可手动上传数据 zip)")

        # 自动检测实际数据目录（处理上传后的子目录结构）
        actual_data_dir = data_dir
        if os.path.isdir(data_dir):
            # 检查是否有子目录
            subdirs = [d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))]
            # 如果只有一个子目录，且该子目录包含数据文件，则使用它
            if len(subdirs) == 1:
                potential_dir = os.path.join(data_dir, subdirs[0])
                # 检查是否包含数据目录
                if os.path.isdir(potential_dir):
                    # 检查是否有数据文件或子目录
                    has_data = False
                    for item in os.listdir(potential_dir):
                        item_path = os.path.join(potential_dir, item)
                        if os.path.isdir(item_path):
                            # 检查目录名是否包含数据类型
                            dir_lower = item.lower()
                            if any(keyword in dir_lower for keyword in ['sentinel', 'landsat', 'aster', 'dem']):
                                has_data = True
                                break
                        elif item.lower().endswith(('.tif', '.tiff')):
                            has_data = True
                            break

                    if has_data:
                        actual_data_dir = potential_dir
                        self.log(f"自动检测到数据目录: {actual_data_dir}")

        # 加载真实数据
        self.log("正在加载真实数据...")
        try:
            # 0. 检测 B10 低分辨率网格 (匹配 MATLAB readgeoraster 行为)
            import glob as _glob
            import rasterio
            target_size = None
            s2_test_dirs = _glob.glob(os.path.join(actual_data_dir, '*', 'Sentinel*2 L2*'))
            if not s2_test_dirs:
                s2_test_dirs = _glob.glob(os.path.join(actual_data_dir, 'Sentinel*2 L2*'))
            if s2_test_dirs:
                b10_files = _glob.glob(os.path.join(s2_test_dirs[0], '*B10*.tif*'))
                b08_files = _glob.glob(os.path.join(s2_test_dirs[0], '*B08*.tif*'))
                if b10_files and b08_files:
                    with rasterio.open(b10_files[0]) as s10, rasterio.open(b08_files[0]) as s08:
                        if s10.shape[0] < s08.shape[0]:
                            target_size = s10.shape
                            self.log(f"使用 B10 网格 {target_size} (B08={s08.shape})")

            # 1. 读取 Sentinel-2 数据
            self.log("加载 Sentinel-2 数据...")
            s2, R, ref_tif_path = read_sentinel2(actual_data_dir, target_size=target_size)
            self.log(f"Sentinel-2 数据加载成功: {s2.shape}")

            # 2. 读取 Landsat-8 数据
            self.log("加载 Landsat-8 数据...")
            lan = read_landsat8(actual_data_dir, R)
            self.log(f"Landsat-8 数据加载成功: {lan.shape}")

            # 3. 读取 ASTER 数据
            self.log("加载 ASTER 数据...")
            ast = read_aster(actual_data_dir, R)
            self.log(f"ASTER 数据加载成功: {ast.shape}")

            # 4. 读取 DEM 和 ROI
            self.log("加载 DEM 和 ROI...")
            dem, inROI, lonGrid, latGrid, lonROI, latROI = read_dem_and_roi(actual_data_dir, roi_file, R)
            self.log(f"DEM 数据加载成功: {dem.shape}")
            self.log(f"ROI 区域包含 {np.sum(inROI)} 个像素")

            # 5. 填充 ASTER 数据的 NaN 值
            self.log("填充 ASTER 数据 NaN 值...")
            ast = fill_aster_nan(ast, inROI)

            # 6. 提取 NIR 和 Red 波段
            self.log("提取波段数据...")
            # s2顺序(0-based): B02(0) B03(1) B04(2) B08(3) B11(4) B12(5) B05(6) B06(7) B07(8)
            # MATLAB getBand(s2,lan,4)=s2(:,:,4)=B08, getBand(s2,lan,3)=s2(:,:,3)=B04
            NIR = get_band(s2, lan, 3)  # 0-indexed: s2[:,:,3] = B08
            Red = get_band(s2, lan, 2)  # 0-indexed: s2[:,:,2] = B04

            # 6.5 Phase 1.5: 自动检测并加载 InSAR 产品(可选,缺失则降级)
            insar_velocity, insar_coherence, insar_meta = self._load_insar(
                actual_data_dir, ref_shape=inROI.shape, ref_tif_path=ref_tif_path
            )
            if insar_velocity is not None:
                self.log(f"InSAR 数据加载成功: 速率 {insar_velocity.shape}, "
                         f"相干性 {insar_coherence.shape if insar_coherence is not None else 'N/A'}")
            else:
                self.log("未检测到 InSAR 数据,SlowVars 第 8 类因素 surface_deformation 将被跳过")

            # 6.6 自动检测并加载 geo-stru 构造解译产物(可选,缺失则降级)
            # D 部分:enabled=True 时走 CRS 重投影 + metadata 稀疏门控,并把构造注入深部
            # fault_activity(因果正确的家)、撤地表乘子;enabled=False 时维持现状(地表乘子、
            # legacy zoom 对齐),零回归。
            scfg = config.get('structural', {}) or {}
            struct_enabled = bool(scfg.get('enabled', False))
            # 跨系统自动匹配:enabled 且 auto_discover 时,从 geo-stru results_root 按 ROI 找构造 run
            extra_struct = []
            if struct_enabled and scfg.get('auto_discover', True) and scfg.get('results_root'):
                try:
                    roi_poly = np.column_stack([lonROI, latROI]) if len(lonROI) >= 3 else None
                    gdir, gov = self._find_structural_dir(
                        scfg['results_root'], roi_poly, float(scfg.get('min_roi_overlap', 0.15)))
                    if gdir:
                        extra_struct.append(gdir)
                        self.log(f"构造自动匹配 geo-stru run(ROI 重叠 {gov:.2f}): {gdir}")
                    else:
                        self.log("构造自动匹配:geo-stru results 中未找到 ROI 重叠达标的 run")
                except Exception as e:
                    self.log(f"构造自动匹配异常,跳过: {e}", 'WARN')
            structural_control, structural_density, structural_meta = self._load_structural(
                actual_data_dir, ref_shape=inROI.shape, ref_tif_path=ref_tif_path,
                lonGrid=lonGrid, latGrid=latGrid, scfg=scfg, extra_candidates=extra_struct
            )
            if structural_control is not None:
                self.log(f"构造产物加载成功: 距断裂邻近度 {structural_control.shape}(构造控矿先验)")
            else:
                reason = (structural_meta or {}).get('skip_reason')
                self.log(f"未启用/未检测到 geo-stru 构造产物{('('+reason+')') if reason else ''},"
                         f"构造控矿因子将被跳过")

            # 深部注入开关:仅 enabled 且 inject_into_faultactivity 时,构造进 slow_vars
            inject_deep = struct_enabled and bool(scfg.get('inject_into_faultactivity', True))
            fault_lineament = None
            if inject_deep:
                fault_lineament = structural_density if structural_density is not None else structural_control
                if fault_lineament is not None:
                    self.log(f"  -> 构造将注入深部 slow_vars.fault_activity(权重 {scfg.get('lineament_weight', 0.5)})")

            # 7. 构建数据上下文
            data_context = {
                'data_dir': data_dir,
                'roi_file': roi_file,
                'mineral_type': config['mineral_type'],
                'inROI': inROI,
                'lonGrid': lonGrid,
                'latGrid': latGrid,
                's2': s2,
                'ast': ast,
                'dem': dem,
                'lan': lan,
                'NIR': NIR,
                'Red': Red,
                'roi_points': roi_data['roi_poly'],
                'lonROI': lonROI,
                'latROI': latROI,
                'ref_tif_path': ref_tif_path,
                'R': R,  # 保存地理参考信息
                # Phase 1.5: InSAR 数据(可选)
                'insar_velocity': insar_velocity,
                'insar_coherence': insar_coherence,
                'insar_meta': insar_meta,
                # geo-stru 构造控矿先验(可选):距断裂邻近度[0,1] + 断裂密度
                'structural_control': structural_control,
                'structural_density': structural_density,
                'structural_meta': structural_meta,
                # D 部分:深部注入通道(仅 enabled+inject 时非 None)+ 权重透传给 slow_vars
                'fault_lineament': fault_lineament,
                'lineament_weight': float(scfg.get('lineament_weight', 0.5)),
            }

            self.log("真实数据加载完成")

        except Exception as e:
            error_msg = f"数据加载失败: {str(e)}"
            self.log(error_msg, 'ERROR')
            raise ValueError(error_msg)

        self.log(f"数据加载完成")

        return data_context

    def _load_insar(self, data_dir: str, ref_shape, ref_tif_path: str = None):
        """
        Phase 1.5: 自动加载 InSAR 数据(LOS 速率 + 相干性)。

        查找逻辑:
        1. data_dir/los_velocity.tif + coherence.tif(直接放数据目录)
        2. data_dir/insar/los_velocity.tif + coherence.tif(insar 子目录)
        3. data_dir/sentinel1_insar/*/los_displacement.tif + coherence.tif
           (geo-insar 标准输出契约,取最新一对)
        4. 也支持 los_velocity.tif 不存在但 los_displacement.tif 存在时,用形变除以时间基线推算速率

        所有找到的 GeoTIFF 都会重采样到 ref_shape。

        Returns
        -------
        (velocity_array, coherence_array, meta_dict) — 都可能为 None
        """
        import glob as _glob
        import rasterio
        from rasterio.warp import reproject, Resampling

        def _read_aligned(path):
            """读 GeoTIFF 并重采样到 ref_shape(简单 nearest 重采样)。"""
            with rasterio.open(path) as src:
                arr = src.read(1).astype(np.float32)
                if arr.shape != ref_shape:
                    # 简单缩放(若需精确地理对齐应该用 reproject;这里 MVP)
                    from scipy.ndimage import zoom
                    zy = ref_shape[0] / arr.shape[0]
                    zx = ref_shape[1] / arr.shape[1]
                    arr = zoom(arr, (zy, zx), order=1)
                return arr

        # 候选目录
        candidates = [
            data_dir,
            os.path.join(data_dir, 'insar'),
            os.path.join(data_dir, 'InSAR'),
        ]
        # geo-insar 标准契约目录
        s1_dirs = _glob.glob(os.path.join(data_dir, 'sentinel1_insar', '*'))
        s1_dirs.extend(_glob.glob(os.path.join(data_dir, '**', 'sentinel1_insar', '*'), recursive=True))
        # 按日期排序,取最新一对(干涉对目录名通常是 YYYYMMDD_YYYYMMDD_POL)
        s1_dirs = sorted(set(s1_dirs))

        velocity = None
        coherence = None
        meta = {}

        # 1) los_velocity.tif 直接搜索
        for c in candidates + s1_dirs:
            if not c or not os.path.isdir(c):
                continue
            v_path = os.path.join(c, 'los_velocity.tif')
            if os.path.exists(v_path):
                try:
                    velocity = _read_aligned(v_path)
                    meta['velocity_path'] = v_path
                    break
                except Exception as e:
                    self.log(f"InSAR los_velocity 读取失败 {v_path}: {e}", 'WARN')

        # 2) 如果没找到速率,但找到形变图,用 los_displacement.tif 除以时间基线估算速率
        if velocity is None and s1_dirs:
            for pair_dir in reversed(s1_dirs):  # 最新优先
                disp_path = os.path.join(pair_dir, 'los_displacement.tif')
                meta_path = os.path.join(pair_dir, 'metadata.json')
                if os.path.exists(disp_path) and os.path.exists(meta_path):
                    try:
                        with open(meta_path, 'r', encoding='utf-8') as f:
                            pair_meta = json.load(f)
                        bl_days = pair_meta.get('temporal_baseline_days')
                        if bl_days and bl_days > 0:
                            disp = _read_aligned(disp_path)  # mm
                            velocity = disp * (365.25 / bl_days)  # mm/year
                            meta['velocity_path'] = disp_path
                            meta['estimated_from_pair'] = True
                            meta['pair_id'] = pair_meta.get('pair_id')
                            break
                    except Exception as e:
                        self.log(f"InSAR 形变→速率推算失败 {disp_path}: {e}", 'WARN')

        # 3) coherence.tif 搜索
        for c in candidates + s1_dirs:
            if not c or not os.path.isdir(c):
                continue
            coh_path = os.path.join(c, 'coherence.tif')
            if os.path.exists(coh_path):
                try:
                    coherence = _read_aligned(coh_path)
                    meta['coherence_path'] = coh_path
                    break
                except Exception as e:
                    self.log(f"InSAR coherence 读取失败 {coh_path}: {e}", 'WARN')

        if velocity is None and coherence is None:
            return None, None, None
        return velocity, coherence, meta

    def _find_structural_dir(self, results_root, roi_poly_lonlat, min_overlap=0.15):
        """跨系统自动匹配 geo-stru 构造 run:按 metadata.json 的 aoi_bbox 与勘探 ROI 空间重叠。

        geo-stru 产物无矿种/deposit_type 概念,仅靠 aoi_bbox 做 ROI 重叠匹配
        (兼容 <项目>/structural/ 扁平 与 <项目>/structural/<run>/ 嵌套两种布局)。
        返回 (含构造产物的目录, 重叠度) 或 (None, 0)。
        """
        if not results_root or not os.path.isdir(results_root) or roi_poly_lonlat is None:
            return None, 0.0
        try:
            from utils.geo_bridge_common import roi_overlap_frac
        except Exception:
            return None, 0.0
        import glob as _glob
        import json as _json
        best = None
        patterns = [os.path.join(results_root, '*', 'structural', 'metadata.json'),
                    os.path.join(results_root, '*', 'structural', '**', 'metadata.json')]
        seen = set()
        for pat in patterns:
            for mpath in _glob.glob(pat, recursive=True):
                if mpath in seen:
                    continue
                seen.add(mpath)
                try:
                    mm = _json.load(open(mpath, encoding='utf-8'))
                    bbox = mm.get('aoi_bbox')
                    if not bbox or len(bbox) != 4:
                        continue
                    x0, y0, x1, y1 = bbox
                    geom = {'type': 'Polygon',
                            'coordinates': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}
                    ov = roi_overlap_frac(roi_poly_lonlat, geom)
                except Exception:
                    continue
                if best is None or ov > best[0]:
                    best = (ov, os.path.dirname(mpath))
        if best and best[0] >= min_overlap:
            return best[1], best[0]
        return None, 0.0

    def _load_structural(self, data_dir: str, ref_shape, ref_tif_path: str = None,
                         lonGrid=None, latGrid=None, scfg=None, extra_candidates=None):
        """
        自动加载 geo-stru 构造解译产物,生成"构造控矿"输入(可选,缺失则降级)。

        查找 distance_to_lineament.tif / lineament_density.tif:
        1. data_dir/structural/ (geo-stru 标准 <AOI>/structural/ 布局)
        2. data_dir/ 直接放置
        3. data_dir/**/structural/ 递归

        返回的 structural_control 为"距断裂邻近度",值域[0,1],近断裂=1,
        作为构造控矿先验(矿化受构造控制,断裂附近异常更可信)。

        对齐方式
        --------
        - scfg.enabled=True 且提供 lonGrid/latGrid:用 CRS+范围感知重投影
          (geo_bridge_common.reproject_to_grid),修正旧版 scipy zoom 假设"AOI 同范围"
          的隐患;并读 metadata.json 的 n_lineaments 做稀疏门控。
        - 否则:维持旧版 scipy zoom 行为(零回归)。

        Returns
        -------
        (structural_control, structural_density, meta) — 都可能为 None
        """
        import glob as _glob
        import rasterio
        scfg = scfg or {}
        enabled = bool(scfg.get('enabled', False))
        use_reproject = enabled and lonGrid is not None and latGrid is not None

        def _read_aligned(path):
            if use_reproject:
                from utils.geo_bridge_common import reproject_to_grid
                return reproject_to_grid(path, lonGrid, latGrid, ref_shape,
                                         inROI=None, resampling='bilinear')
            with rasterio.open(path) as src:
                arr = src.read(1).astype(np.float32)
            if arr.shape != ref_shape:
                from scipy.ndimage import zoom
                arr = zoom(arr, (ref_shape[0] / arr.shape[0],
                                 ref_shape[1] / arr.shape[1]), order=1)
            return arr

        # 候选目录:兼容两种 geo-stru 布局 —— 扁平 <AOI>/structural/ 与
        # 嵌套 <AOI>/structural/<timestamp>_struct_0000/。递归按"含构造产物的目录"收集,
        # 时间戳目录按名倒序(优先最新)。
        candidates = [os.path.join(data_dir, 'structural'), data_dir]
        nested = set()
        for marker in ('distance_to_lineament.tif', 'lineament_density.tif', 'metadata.json'):
            for p in _glob.glob(os.path.join(data_dir, '**', marker), recursive=True):
                nested.add(os.path.dirname(p))
        candidates.extend(sorted(nested, reverse=True))
        # 跨系统自动匹配命中的 geo-stru 目录(优先级低于上传 data_dir 内的产物)
        if extra_candidates:
            candidates.extend(extra_candidates)
        # 去重保序
        _seen = set()
        candidates = [c for c in candidates if c and not (c in _seen or _seen.add(c))]

        dist = None
        density = None
        meta = {}

        # 稀疏度门控(仅 enabled):读 metadata.json 的 structural_stats.n_lineaments,
        # 太少(如本溪=0)则不注入,fault_activity 维持 Canny-only。
        if enabled:
            min_lin = int(scfg.get('min_lineaments', 1))
            for c in candidates:
                if not c or not os.path.isdir(c):
                    continue
                mpath = os.path.join(c, 'metadata.json')
                if os.path.exists(mpath):
                    try:
                        import json as _json
                        mm = _json.load(open(mpath, encoding='utf-8'))
                        if 'structural_stats' not in mm:
                            continue  # 非 geo-stru 构造 metadata,不据此门控
                        n_lin = int((mm.get('structural_stats') or {}).get('n_lineaments', 0))
                        meta['n_lineaments'] = n_lin
                        if n_lin < min_lin:
                            meta['skip_reason'] = f"构造稀疏 n_lineaments={n_lin}<{min_lin}"
                            self.log(f"构造门控:{meta['skip_reason']},跳过构造注入")
                            return None, None, meta
                        break  # 已找到有效构造 metadata
                    except Exception as e:
                        self.log(f"构造 metadata 读取失败 {mpath}: {e}", 'WARN')

        for c in candidates:
            if not c or not os.path.isdir(c):
                continue
            d_path = os.path.join(c, 'distance_to_lineament.tif')
            if dist is None and os.path.exists(d_path):
                try:
                    dist = _read_aligned(d_path)
                    meta['distance_path'] = d_path
                except Exception as e:
                    self.log(f"构造 distance 读取失败 {d_path}: {e}", 'WARN')
            den_path = os.path.join(c, 'lineament_density.tif')
            if density is None and os.path.exists(den_path):
                try:
                    density = _read_aligned(den_path)
                    meta['density_path'] = den_path
                except Exception as e:
                    self.log(f"构造 density 读取失败 {den_path}: {e}", 'WARN')
            if dist is not None and density is not None:
                break

        if dist is None and density is None:
            return None, None, (meta or None)

        # 距断裂距离 → 邻近度[0,1](指数衰减,尺度=有效距离的中位数)
        structural_control = None
        if dist is not None:
            valid = np.isfinite(dist)
            if valid.any():
                scale = np.nanmedian(dist[valid]) or 1.0
                structural_control = np.where(valid, np.exp(-dist / (scale + 1e-9)), 0.0).astype(np.float32)
        elif density is not None:
            dmax = np.nanmax(density) or 1.0
            structural_control = np.clip(density / dmax, 0, 1).astype(np.float32)

        return structural_control, density, meta

    def _init_fusion_engine(self, config: Dict) -> FusionEngine:
        """初始化融合引擎"""
        detectors = {}
        for detector_name in config.get('detectors', []):
            detectors[detector_name] = None

        return FusionEngine(detectors)

    def _post_process(self, data_context: Dict, fusion_engine: FusionEngine,
                     final_mask: np.ndarray, config: Dict) -> Dict:
        """
        后处理流程 — 严格按照 Matlab PostProcessor.run() 实现

        包含: 深度/压力反演、地表潜力计算、PCA、增强函数、高斯滤波、融合
        """
        self.log("执行后处理 (复刻 Matlab PostProcessor.run)...")

        inROI = data_context['inROI']
        H, W = inROI.shape
        mineral_type = config['mineral_type']
        eps_val = 1e-6

        # 创建结果目录
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_dir = os.path.join(config['out_dir'], f"mineral_analysis_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)

        # ====== 蚀变接入(geo-analyser):B 地表项升级 + A 旁证重排,默认关 ======
        # 蚀变是地表"结果"非深部"驱动":只升级地表项 / 重排 Top-20,绝不进 final_mask/深部物理。
        alt_layers = None
        alt_overlay = None
        alt_report = {}
        try:
            alt_cfg = config.get('alteration', {}) or {}
            if alt_cfg.get('enabled'):
                from utils.alteration_bridge import load_alteration_for_run
                alt_layers = load_alteration_for_run(
                    mineral_type,
                    data_context['lonGrid'], data_context['latGrid'], inROI,
                    np.asarray(data_context.get('roi_points')),
                    alt_cfg, log=self.log,
                )
        except Exception as e:
            self.log(f"蚀变接入加载异常,降级: {e}", 'WARN')

        # --- 安全获取探测器 debug 数据 ---
        def safe_get(name):
            if name in fusion_engine.results:
                return fusion_engine.results[name]
            class _EmptyRes:
                mask = np.zeros((H, W), dtype=np.float32)
                debug_data = {'F_map': np.zeros((H, W)), 'delta_red_edge': np.zeros((H, W)),
                              'moran_local': np.zeros((H, W)), 'F_abs': np.zeros((H, W))}
            return _EmptyRes()

        res_Red = safe_get('red_edge')
        res_Int = safe_get('intrinsic')
        res_Slow = safe_get('slow_vars')

        anomaly_mask_rededge = res_Red.mask
        anomaly_mask_fabs = res_Int.mask
        anomaly_mask_slow = res_Slow.mask

        F_map = res_Red.debug_data.get('F_map', np.zeros((H, W)))
        delta_red = res_Red.debug_data.get('delta_red_edge', np.zeros((H, W)))
        moran_local = res_Int.debug_data.get('moran_local', np.zeros((H, W)))
        F_abs = res_Int.debug_data.get('F_abs', np.zeros((H, W)))

        # ====== 1. 深度与压力反演 (Yakymchuk) ======
        self.log("  -> 深度/压力反演...")
        params = get_yakymchuk_params(mineral_type)
        a, b, c_param = params['a'], params['b'], params['c']

        f_res_MHz = a + b * np.exp(-c_param * np.abs(F_map))
        f_res_MHz[np.isnan(f_res_MHz)] = a
        f_res_MHz[f_res_MHz < 0] = a
        f_res_MHz[~inROI] = np.nan

        c_light = 3e8
        epsilon_r = 16
        depth_map = c_light / (2 * f_res_MHz * 1e6 * np.sqrt(epsilon_r)) / 1000
        depth_map = np.clip(depth_map, 0, 4)
        depth_map[~inROI] = np.nan

        grad_P = 25 + 5 * depth_map
        grad_P = np.clip(grad_P, 0, 40)
        grad_P[~inROI] = np.nan

        # ====== 2. 地表潜力变量 ======
        self.log("  -> 地表潜力变量计算...")
        ast = data_context['ast']
        NIR = data_context['NIR']
        Red = data_context['Red']

        Ferric = mat2gray_roi(ast[:, :, 1] / (ast[:, :, 0] + eps_val), inROI)
        Clay = mat2gray_roi(ast[:, :, 5] / (ast[:, :, 6] + eps_val), inROI)
        # NDVI_inv: 裁剪到 [0, 0.82] 消除卫星条带边缘的极端值
        # (匹配 MATLAB 中无极端 NDVI_inv 值时的归一化行为)
        NDVI_inv_raw = 1 - (NIR - Red) / (NIR + Red + eps_val)
        NDVI_inv_raw = np.clip(NDVI_inv_raw, 0, 0.87)
        NDVI_inv = mat2gray_roi(NDVI_inv_raw, inROI)

        # PCA: ast B4-B7 (0-indexed: 3,4,5,6) — 严格匹配 MATLAB pca()
        pca_input = np.stack([ast[:, :, 3], ast[:, :, 4], ast[:, :, 5], ast[:, :, 6]], axis=-1)
        pca_2d = pca_input.reshape(-1, 4).astype(np.float64)
        pca_mean = np.nanmean(pca_2d, axis=0)
        pca_std = np.nanstd(pca_2d, axis=0, ddof=1)
        pca_std[pca_std == 0] = eps_val
        pca_2d = (pca_2d - pca_mean) / pca_std
        pca_2d[np.isnan(pca_2d)] = 0

        # MATLAB pca() 默认 Centered=true，会再次减去列均值后做 SVD
        col_means = np.mean(pca_2d, axis=0)
        pca_centered = pca_2d - col_means
        U, S, Vt = np.linalg.svd(pca_centered, full_matrices=False)
        score = U * S  # shape: (H*W, 4) — 等同于 MATLAB pca 的 score

        # 符号约定：确保每个主成分的载荷向量中最大绝对值元素为正
        # (匹配 MATLAB pca 的符号约定，消除 SVD 符号不确定性)
        for i in range(Vt.shape[0]):
            max_idx = np.argmax(np.abs(Vt[i, :]))
            if Vt[i, max_idx] < 0:
                Vt[i, :] *= -1
                score[:, i] *= -1

        score_3d = score.reshape(H, W, 4)

        Hydroxy_anomaly = mat2gray_roi(score_3d[:, :, 1], inROI)
        Fe_anomaly = mat2gray_roi(score_3d[:, :, 2], inROI)

        # 诊断日志
        self.log(f"  -> PCA PC2 raw ROI mean={np.nanmean(score_3d[:,:,1][inROI]):.6f}, std={np.nanstd(score_3d[:,:,1][inROI]):.6f}")
        self.log(f"  -> NIR ROI mean={np.nanmean(NIR[inROI]):.6f}, Red ROI mean={np.nanmean(Red[inROI]):.6f}")
        ndvi_raw = (NIR - Red) / (NIR + Red + eps_val)
        self.log(f"  -> NDVI raw ROI mean={np.nanmean(ndvi_raw[inROI]):.6f}")

        # ====== B: 用 geo-analyser 蚀变图升级地表潜力糙代理(因果层级相同;逐项回退)======
        if alt_layers is not None and (config.get('alteration', {}) or {}).get('mode_B_surface', True):
            try:
                from utils.alteration_bridge import apply_surface_upgrade
                _proxies = {'Ferric': Ferric, 'Clay': Clay, 'Hydroxy_anomaly': Hydroxy_anomaly,
                            'Fe_anomaly': Fe_anomaly, 'NDVI_inv': NDVI_inv}
                _proxies, alt_report = apply_surface_upgrade(_proxies, alt_layers, mineral_type, inROI, log=self.log)
                Ferric, Clay = _proxies['Ferric'], _proxies['Clay']
                Hydroxy_anomaly, Fe_anomaly, NDVI_inv = (_proxies['Hydroxy_anomaly'],
                                                         _proxies['Fe_anomaly'], _proxies['NDVI_inv'])
            except Exception as e:
                self.log(f"B 地表项升级异常,保留原代理: {e}", 'WARN')

        # ====== 3. 矿种专属增强函数 ======
        self.log("  -> 地表潜力增强函数...")
        _, _, _, enh_func = get_mineral_thresholds(mineral_type)

        Au_surface = np.zeros((H, W), dtype=np.float64)
        if mineral_type == 'cave':
            # cave模式需要 slope 和 neg_curvature
            from utils.geo_utils import mat2gray_roi as _m2g
            gx_dem, gy_dem = np.gradient(data_context['dem'])
            slope = np.arctan(np.sqrt(gx_dem**2 + gy_dem**2)) * 180 / np.pi
            slope = _m2g(slope, inROI)
            dxx, _ = np.gradient(gx_dem)
            _, dyy = np.gradient(gy_dem)
            neg_curv = np.maximum(-(dxx + dyy), 0)
            neg_curv = _m2g(neg_curv, inROI)
            Au_surface = 0.30*NDVI_inv + 0.25*slope + 0.20*neg_curv + 0.15*Hydroxy_anomaly + 0.10*Clay
            Au_surface = mat2gray_roi(Au_surface, inROI)
        elif enh_func is not None:
            Au_surface = enh_func(Ferric, Fe_anomaly, Hydroxy_anomaly, Clay, NDVI_inv)
            Au_surface = mat2gray_roi(Au_surface, inROI)
            self.log(f"  -> NDVI_inv ROI mean={np.nanmean(NDVI_inv[inROI]):.6f}, Hydroxy ROI mean={np.nanmean(Hydroxy_anomaly[inROI]):.6f}")
            self.log(f"  -> Au_surface raw ROI mean={np.nanmean(Au_surface[inROI]):.6f}, max={np.nanmax(Au_surface[inROI]):.6f}")
        else:
            Au_surface = enh_func(Ferric, Fe_anomaly, Hydroxy_anomaly, Clay, NDVI_inv) if enh_func else \
                         0.45*Ferric + 0.25*Fe_anomaly + 0.15*Hydroxy_anomaly + 0.10*Clay + 0.05*NDVI_inv
            Au_surface = mat2gray_roi(Au_surface, inROI)

        # ====== 4. Filter 1 — 归一化高斯滤波消除边缘效应 ======
        self.log("  -> 高斯滤波 (sigma=8)...")
        valid_mask = inROI & ~np.isnan(Au_surface)
        Au_temp = Au_surface.copy()
        Au_temp[~valid_mask] = 0

        Au_filt = gaussian_filter(Au_temp, sigma=8, mode='nearest', truncate=2.0)
        W_filt = gaussian_filter(valid_mask.astype(np.float64), sigma=8, mode='nearest', truncate=2.0)
        W_filt[W_filt == 0] = eps_val

        Au_surface[valid_mask] = Au_filt[valid_mask] / W_filt[valid_mask]
        Au_surface = mat2gray_roi(Au_surface, inROI)
        self.log(f"  -> Filter1 后 Au_surface ROI mean={np.nanmean(Au_surface[inROI]):.6f}, max={np.nanmax(Au_surface[inROI]):.6f}")

        # 拦截空掩码
        if final_mask is None or final_mask.size == 0:
            final_mask = np.zeros_like(Au_surface)
        if final_mask.shape != Au_surface.shape:
            from scipy.ndimage import zoom as scipy_zoom
            zh, zw = Au_surface.shape[0] / final_mask.shape[0], Au_surface.shape[1] / final_mask.shape[1]
            final_mask = scipy_zoom(final_mask, (zh, zw), order=0)

        # ====== 5. 融合模式 ======
        fusion_mode = config.get('fusion_mode', True)
        if not fusion_mode:
            self.log("  -> [纯净模式] 跳过地表背景")
            Au_deep = mat2gray_roi(final_mask, inROI)
            Au_deep[~inROI] = np.nan
        else:
            self.log("  -> [融合模式] 叠加地表背景...")
            Au_surface[inROI] = Au_surface[inROI] * (1 + final_mask[inROI] * 0.4)
            Au_surface[inROI & (np.isnan(Au_surface) | np.isinf(Au_surface))] = 0

            # geo-stru 构造控矿先验(可选):矿化受构造控制,近断裂处上调、远断裂轻度下调。
            # D 部分门控:STRUCTURAL.enabled=True 时,构造已注入深部 fault_activity,
            # 这里默认撤掉地表乘子防双计(structural_in_surface=False);enabled=False 时
            # 维持现状(地表乘子 ON),零回归。
            _scfg = config.get('structural', {}) or {}
            if _scfg.get('enabled', False):
                surface_mult = bool(_scfg.get('structural_in_surface', False))
            else:
                surface_mult = True  # 现状:无 config 时照旧叠加
            sc = data_context.get('structural_control')
            if sc is not None and surface_mult:
                w = float(_scfg.get('structural_weight', config.get('structural_weight', 0.12)))
                if sc.shape != Au_surface.shape:
                    from scipy.ndimage import zoom as _sc_zoom
                    sc = _sc_zoom(sc, (Au_surface.shape[0] / sc.shape[0],
                                       Au_surface.shape[1] / sc.shape[1]), order=1)
                sc = np.clip(np.nan_to_num(sc, nan=0.0), 0, 1)
                Au_surface[inROI] = Au_surface[inROI] * (1 - w + w * sc[inROI])
                self.log(f"  -> 已叠加 geo-stru 构造控矿先验(地表乘子,权重 {w})")
            elif sc is not None and not surface_mult:
                self.log("  -> 构造已注入深部 fault_activity,地表乘子按配置撤除(防双计)")

            # Filter 2
            valid_mask2 = inROI & ~np.isnan(Au_surface)
            Au_temp2 = Au_surface.copy()
            Au_temp2[~valid_mask2] = 0

            Au_filt2 = gaussian_filter(Au_temp2, sigma=6, mode='nearest', truncate=2.0)
            W_filt2 = gaussian_filter(valid_mask2.astype(np.float64), sigma=6, mode='nearest', truncate=2.0)
            W_filt2[W_filt2 == 0] = eps_val

            Au_surface[valid_mask2] = Au_filt2[valid_mask2] / W_filt2[valid_mask2]
            Au_deep = mat2gray_roi(Au_surface, inROI)
            Au_deep[~inROI] = np.nan
            self.log(f"  -> Au_deep ROI mean={np.nanmean(Au_deep[inROI]):.6f}, max={np.nanmax(Au_deep[inROI]):.6f}, std={np.nanstd(Au_deep[inROI]):.6f}")
            self.log(f"  -> Au_deep >0.4: {np.sum(Au_deep[inROI]>0.4)}, >0.6: {np.sum(Au_deep[inROI]>0.6)}, >0.8: {np.sum(Au_deep[inROI]>0.8)}")

        # ====== A: 蚀变一致性叠层 + Top-20 重排(可选;不改 Au_deep / 深部物理)======
        rank_field = Au_deep
        if alt_layers is not None and (config.get('alteration', {}) or {}).get('mode_A_rerank', True):
            try:
                from utils.alteration_bridge import compute_consistency_overlay
                alt_overlay = compute_consistency_overlay(Au_deep, alt_layers, inROI)
                if alt_overlay is not None and not alt_layers.weak:
                    rank_field = alt_overlay['rerank_score']
                    self.log("  -> A: 用地表蚀变佐证度重排 Top-20(Au_deep/深部物理不变)")
                elif alt_overlay is not None:
                    self.log("  -> A: 弱 run,仅产出佐证叠层,不重排 Top-20")
            except Exception as e:
                self.log(f"A 重排异常,保留原排序: {e}", 'WARN')

        # ====== 6. Top 20 ======
        lonGrid = data_context['lonGrid']
        latGrid = data_context['latGrid']
        # inROI/Au_deep 是"北朝上"帧(inROI=flipud(inROI_grid)),取靶点经纬度时两者都要 flipud。
        # 可分离网格下 flipud(lonGrid)==lonGrid(经度只随列变),故对原 EPSG:4326 数据零回归;
        # 重投影网格(UTM->经纬度)经度同时随行列变化,必须翻转经度才能与纬度配对正确。
        lonGrid_corrected = np.flipud(lonGrid)
        latGrid_corrected = np.flipud(latGrid)

        def _pick_top(field, k):
            t = np.array(field, dtype=np.float64, copy=True)
            t[~inROI] = 0
            t[np.isnan(t)] = 0
            fl = t.ravel()
            kk = min(k, len(fl))
            ti = np.argpartition(fl, -kk)[-kk:]
            ti = ti[np.argsort(fl[ti])[::-1]]
            return np.unravel_index(ti, (H, W))

        topY, topX = _pick_top(rank_field, 20)
        lonTop = lonGrid_corrected[topY, topX]
        latTop = latGrid_corrected[topY, topX]
        redIdx = list(range(len(topY)))

        # 重排时另存原始(纯 Au_deep)Top-20 供回溯
        orig_lonTop = orig_latTop = None
        if alt_overlay is not None and rank_field is not Au_deep:
            oY, oX = _pick_top(Au_deep, 20)
            orig_lonTop = lonGrid_corrected[oY, oX]
            orig_latTop = latGrid_corrected[oY, oX]

        # ====== 7. 保存结果 ======
        threshold = config.get('kmz_threshold', 0.6)

        # 保存中间变量供可视化使用
        post_data = {
            'Au_deep': Au_deep,
            'F_map': F_map, 'delta_red': delta_red, 'moran_local': moran_local,
            'depth_map': depth_map, 'grad_P': grad_P, 'f_res_MHz': f_res_MHz,
            'final_mask': final_mask, 'F_abs': F_abs,
            'anomaly_mask_rededge': anomaly_mask_rededge,
            'anomaly_mask_fabs': anomaly_mask_fabs,
            'anomaly_mask_slow': anomaly_mask_slow,
            'lonTop': lonTop, 'latTop': latTop, 'redIdx': redIdx,
            'lonGrid': lonGrid, 'latGrid': latGrid,
            'lonROI': data_context['lonROI'], 'latROI': data_context['latROI'],
        }

        # 蚀变接入产物(可选)并入 post_data 供可视化
        if alt_overlay is not None:
            post_data['alteration_score'] = alt_overlay.get('alteration_score')
            post_data['corroboration'] = alt_overlay.get('corroboration')
        if alt_layers is not None:
            post_data['alteration_run_id'] = alt_layers.run_id

        results = {
            'task_id': f"task_{timestamp}",
            'mineral_type': mineral_type,
            'fusion_mode': fusion_mode,
            'kmz_threshold': threshold,
            'task_name': config.get('task_name', ''),
            'result_dir': result_dir,
            'output_files': {},
            'statistics': {
                'max_value': float(np.nanmax(Au_deep)),
                'min_value': float(np.nanmin(Au_deep)),
                'mean_value': float(np.nanmean(Au_deep)),
                'std_value': float(np.nanstd(Au_deep)),
                'area_threshold': float(np.sum(Au_deep > threshold))
            },
            'post_data': post_data,
        }

        # 蚀变接入统计(可选)
        if alt_overlay is not None:
            _corr = alt_overlay.get('corroboration')
            results['statistics']['alteration_corroboration_mean'] = (
                float(np.nanmean(_corr[inROI])) if _corr is not None else 0.0)
            results['statistics']['alteration_run_id'] = alt_layers.run_id if alt_layers else ''
            if orig_lonTop is not None:
                _a = set(zip(np.round(lonTop, 6), np.round(latTop, 6)))
                _b = set(zip(np.round(orig_lonTop, 6), np.round(orig_latTop, 6)))
                results['statistics']['top20_rerank_changed'] = len(_a ^ _b) // 2

        # 保存 .mat 兼容结果
        from scipy.io import savemat
        mat_data = {
            'Au_deep': np.nan_to_num(Au_deep, nan=0).astype(np.float32),
            'F_abs': np.nan_to_num(F_abs, nan=0).astype(np.float32),
            'anomaly_mask_fabs': anomaly_mask_fabs.astype(np.float32),
            'anomaly_mask_rededge': anomaly_mask_rededge.astype(np.float32),
            'anomaly_mask_slow': anomaly_mask_slow.astype(np.float32),
            'depth_map': np.nan_to_num(depth_map, nan=0).astype(np.float32),
            'f_res_MHz': np.nan_to_num(f_res_MHz, nan=0).astype(np.float32),
            'final_anomaly_mask': final_mask.astype(np.float32),
            'inROI': inROI.astype(np.uint8),
            'latGrid': latGrid.astype(np.float64),
            'lonGrid': lonGrid.astype(np.float64),
            'latROI': data_context['latROI'].astype(np.float64),
            'lonROI': data_context['lonROI'].astype(np.float64),
            'latTop': latTop.astype(np.float64),
            'lonTop': lonTop.astype(np.float64),
            'mineral_type': mineral_type,
            'moran_local': np.nan_to_num(moran_local, nan=0).astype(np.float32),
            'redIdx': np.array(redIdx, dtype=np.float64),
            'kmz_threshold': float(threshold),
            # 诊断中间变量
            'NDVI_inv_raw': np.nan_to_num(1 - (NIR - Red) / (NIR + Red + eps_val), nan=0).astype(np.float32),
            'Hydroxy_anomaly': Hydroxy_anomaly.astype(np.float32),
            'Fe_anomaly': Fe_anomaly.astype(np.float32),
            'Ferric': Ferric.astype(np.float32),
            'Clay': Clay.astype(np.float32),
            'NIR_band': np.nan_to_num(NIR, nan=0).astype(np.float32),
            'Red_band': np.nan_to_num(Red, nan=0).astype(np.float32),
            'PCA_PC2_raw': np.nan_to_num(score_3d[:,:,1], nan=0).astype(np.float32),
            'PCA_PC3_raw': np.nan_to_num(score_3d[:,:,2], nan=0).astype(np.float32),
        }
        # 蚀变接入:并入 .mat(追加键,不动现有键;现有 lonTop/latTop 为重排后值,orig_* 可回溯)
        if alt_layers is not None:
            import json as _json
            mat_data['alteration_run_id'] = alt_layers.run_id
            mat_data['surface_source'] = _json.dumps(alt_report, ensure_ascii=False) if alt_report else ''
        if alt_overlay is not None:
            for _k in ('alteration_score', 'corroboration', 'rerank_score'):
                _v = alt_overlay.get(_k)
                if _v is not None:
                    mat_data[_k] = np.nan_to_num(_v, nan=0).astype(np.float32)
            if orig_lonTop is not None:
                mat_data['orig_lonTop'] = np.asarray(orig_lonTop, dtype=np.float64)
                mat_data['orig_latTop'] = np.asarray(orig_latTop, dtype=np.float64)
        mat_file = os.path.join(result_dir, f'{mineral_type}_Result.mat')
        savemat(mat_file, mat_data)
        self.log(f"  结果 .mat 已保存: {mat_file}")

        np.save(os.path.join(result_dir, 'final_mask.npy'), final_mask)
        np.save(os.path.join(result_dir, 'Au_deep.npy'), Au_deep)
        # 蚀变接入产物 .npy(可选)
        if alt_overlay is not None:
            for _k in ('alteration_score', 'corroboration', 'rerank_score'):
                _v = alt_overlay.get(_k)
                if _v is not None:
                    np.save(os.path.join(result_dir, f'{_k}.npy'), _v)

        # ====== 8. 写出机读契约 metadata.json（供 geo-reporter 等下游 broker 订阅）======
        try:
            lonROI = data_context['lonROI']
            latROI = data_context['latROI']
            aoi_bbox = [float(np.min(lonROI)), float(np.min(latROI)),
                        float(np.max(lonROI)), float(np.max(latROI))]
            _vals = Au_deep[topY, topX]
            prospecting_targets = [
                {"rank": i + 1,
                 "longitude": float(lonTop[i]),
                 "latitude": float(latTop[i]),
                 "value": float(_vals[i])}
                for i in range(len(lonTop))
            ]
            metadata = {
                "source": "geo-exploration",
                "analysis_type": "矿产深部探测",
                "created_at": datetime.now().isoformat(),
                "task_id": results['task_id'],
                "run_id": os.path.basename(result_dir),
                "mineral_type": mineral_type,
                "task_name": config.get('task_name', ''),
                "aoi_name": config.get('task_name', '') or os.path.basename(os.path.dirname(result_dir)),
                "aoi_bbox": aoi_bbox,
                "crs": "EPSG:4326",
                "fusion_mode": fusion_mode,
                "kmz_threshold": float(threshold),
                "statistics": results['statistics'],
                "products": {
                    "resonance_map": "01_共振参数综合图.png",
                    "fusion_mask": "02_掩码集成.png",
                    "deep_prediction_map": "03_深部成矿预测图.png",
                    "depth_map": "04_深度反演图.png",
                    "pressure_map": "05_压力反演图.png",
                    "kmz_file": "mineral_prediction.kmz",
                    "mat_file": f"{mineral_type}_Result.mat",
                },
                "prospecting_targets": prospecting_targets,
            }
            # 决策轨迹血缘三键（容错，不影响产物）：显式 trace_id 优先 → 自生成
            try:
                from commons.trace import stamp_metadata
                stamp_metadata(metadata, explicit_trace_id=config.get('trace_id'), tenant_id=config.get('tenant_id'))
            except Exception:
                pass
            with open(os.path.join(result_dir, 'metadata.json'), 'w', encoding='utf-8') as _f:
                json.dump(metadata, _f, ensure_ascii=False, indent=2)
            self.log("  机读契约 metadata.json 已保存")
        except Exception as _e:
            self.log(f"  metadata.json 写出失败（不影响主流程）: {_e}", 'WARN')

        self.log(f"后处理完成，结果保存到: {result_dir}")
        return results

    def _generate_visualizations(self, results: Dict, config: Dict,
                                 data_context: Dict, fusion_engine: FusionEngine):
        """生成可视化结果 — 使用真实的后处理数据"""
        from utils.visualizer import Visualizer
        import matplotlib
        matplotlib.use('Agg')

        self.log("生成可视化图表...")

        result_dir = results['result_dir']
        H, W = data_context['inROI'].shape
        post_data = results.get('post_data', {})

        # 从后处理数据中获取真实值
        F_map = post_data.get('F_map', np.zeros((H, W)))
        delta_red = post_data.get('delta_red', np.zeros((H, W)))
        moran = post_data.get('moran_local', np.zeros((H, W)))
        mask = post_data.get('final_mask', np.zeros((H, W)))
        depth = post_data.get('depth_map', np.zeros((H, W)))
        gradP = post_data.get('grad_P', np.zeros((H, W)))
        freq = post_data.get('f_res_MHz', np.zeros((H, W)))
        Au_deep = post_data.get('Au_deep', np.zeros((H, W)))
        lonTop = post_data.get('lonTop', np.array([0.0]))
        latTop = post_data.get('latTop', np.array([0.0]))
        redIdx = post_data.get('redIdx', [0])

        lonGrid = data_context['lonGrid']
        latGrid = data_context['latGrid']
        lonR = data_context['lonROI']
        latR = data_context['latROI']

        # RGB 图像
        RGB = np.stack([data_context['s2'][:, :, 2], data_context['s2'][:, :, 1], data_context['s2'][:, :, 0]], axis=-1)

        # 从探测器获取各掩码
        red_edge_debug = fusion_engine.results.get('red_edge', None)
        intrinsic_debug = fusion_engine.results.get('intrinsic', None)
        slow_vars_debug = fusion_engine.results.get('slow_vars', None)

        # 1. 生成共振参数综合图
        self.log("生成共振参数综合图...")
        try:
            resonance_path = Visualizer.run_resonance(
                F_map, delta_red, moran, mask, depth*1000, gradP, freq, RGB,
                result_dir, lonGrid, latGrid
            )
            self.log(f"✓ 共振参数图已生成: {os.path.basename(resonance_path)}")
            results['output_files']['resonance_map'] = os.path.basename(resonance_path)
        except Exception as e:
            self.log(f"✗ 生成共振参数图失败: {str(e)}", 'ERROR')

        # 2. 生成掩码集成图
        self.log("生成掩码集成图...")
        try:
            mask_list = []
            titles = []

            if red_edge_debug:
                mask_list.append(red_edge_debug.mask)
                titles.append('1.红边异常')
            if intrinsic_debug:
                mask_list.append(intrinsic_debug.mask)
                titles.append('2.本征吸收')
            if slow_vars_debug:
                mask_list.append(slow_vars_debug.mask)
                titles.append('3.慢变量突变')
            mask_list.append(mask)
            titles.append('4.融合结果')

            if mask_list:
                fusion_path = Visualizer.run_mask_fusion(
                    mask_list, titles, lonGrid, latGrid, result_dir
                )
                if fusion_path:
                    self.log(f"✓ 掩码集成图已生成: {os.path.basename(fusion_path)}")
                    results['output_files']['fusion_map'] = os.path.basename(fusion_path)
        except Exception as e:
            self.log(f"✗ 生成掩码集成图失败: {str(e)}", 'ERROR')

        # 3. 生成深部预测图
        self.log("生成深部预测图...")
        try:
            valid_mask = Au_deep > 1e-10
            valid_count = int(np.sum(valid_mask))
            self.log(f"  Au_deep 有效像素: {valid_count}/{Au_deep.size}, "
                     f"min={np.nanmin(Au_deep):.4f}, max={np.nanmax(Au_deep):.4f}, "
                     f"mean={np.nanmean(Au_deep):.4f}")
            self.log(f"  lonR={len(lonR)}pts, lonTop={len(lonTop)}pts, redIdx={len(redIdx)}")
            prediction_path = Visualizer.run_deep_prediction(
                Au_deep, lonGrid, latGrid, lonR, latR, lonTop, latTop, redIdx,
                config['mineral_type'], result_dir
            )
            self.log(f"✓ 深部预测图已生成: {os.path.basename(prediction_path)}")
            results['output_files']['prediction_map'] = os.path.basename(prediction_path)
        except Exception as e:
            import traceback
            self.log(f"✗ 生成深部预测图失败: {str(e)}", 'ERROR')
            self.log(traceback.format_exc(), 'ERROR')

        # 4. 生成 KMZ (复用 chengjie_matlab_code 的导出逻辑)
        self.log("生成 KMZ 文件...")
        try:
            mat_file = os.path.join(result_dir, f"{config['mineral_type']}_Result.mat")
            if os.path.exists(mat_file):
                from utils.geo_utils import export_kmz_from_mat
                export_kmz_from_mat(mat_file, result_dir)
                self.log("✓ KMZ 文件已生成")
            else:
                self.log("⚠ .mat 文件不存在，跳过 KMZ 生成")
        except Exception as e:
            self.log(f"✗ 生成 KMZ 失败: {str(e)}", 'ERROR')

        self.log("可视化生成完成")

    def get_detector_info(self, detector_name: str) -> Dict:
        """获取探测器信息"""
        detector_info = {
            'red_edge': {
                'name': '红边异常检测器',
                'description': '基于红边位置偏移和 Moran I 空间自相关计算异常强度',
                'parameters': {
                    's2rep_center': 705,
                    'levashov_mode': True
                }
            },
            'intrinsic': {
                'name': '本征吸收检测器',
                'description': '基于矿物特征光谱吸收，生成连续热力图梯度面',
                'parameters': {
                    'weight_ratio': [0.6, 0.4],
                    'gaussian_sigma': 4
                }
            },
            'slow_vars': {
                'name': '慢变量检测器',
                'description': '综合地应力、氧化还原、流体超压等多个地质构造因素',
                'parameters': {
                    'factors': ['stress', 'redox', 'pressure', 'fracture', 'caprock', 'gradient', 'chemical_potential']
                }
            },
            'known_anomaly': {
                'name': '已知异常集成器',
                'description': '集成 KML/KMZ 已知矿点数据，与遥感数据对齐'
            }
        }

        return detector_info.get(detector_name, {})

    def _read_roi_robust(self, filepath):
        """智能读取ROI坐标文件"""
        # 如果 filepath 是目录，查找其中的文件
        if os.path.isdir(filepath):
            files = os.listdir(filepath)
            # 查找可能的 Excel 或 CSV 文件
            for file in files:
                file_path = os.path.join(filepath, file)
                if os.path.isfile(file_path):
                    filepath = file_path
                    self.log(f"在目录中找到ROI文件: {filepath}")
                    break

        ext = os.path.splitext(filepath)[1].lower()
        self.log(f"正在读取ROI文件: {filepath}, 检测到扩展名: {ext}")

        # KML/OVKML(奥维 KML)分支:坐标在 <coordinates> 标签里,复用 geo_utils 的解析。
        # (交付库 ROI 即 .ovkml;此前本方法会把它误判为 CSV。)
        if ext in ('.kml', '.ovkml'):
            from utils.geo_utils import _parse_kml_coordinates
            lon_roi, lat_roi = _parse_kml_coordinates(filepath)
            roi_poly = np.column_stack([lon_roi, lat_roi])
            if len(roi_poly) > 1 and not np.array_equal(roi_poly[0], roi_poly[-1]):
                roi_poly = np.vstack([roi_poly, roi_poly[0]])
            self.log(f"KML/OVKML 解析成功: {len(lon_roi)} 个坐标点")
            return {'roi_poly': roi_poly, 'lon_roi': lon_roi, 'lat_roi': lat_roi}

        # 文件类型检测
        if ext not in ('.xlsx', '.xls', '.csv', '.zip'):
            with open(filepath, 'rb') as f:
                header = f.read(8)
                if header.startswith(b'PK\x03\x04'):
                    ext = '.xlsx'
                    self.log("通过文件头检测为 Excel 文件")
                elif b'\x00' not in header[:100]:
                    ext = '.csv'
                    self.log("通过文件头检测为 CSV 文件")
                else:
                    ext = '.xlsx'
                    self.log("默认按 Excel 文件处理")

        # ZIP 文件处理
        if ext == '.zip':
            import zipfile
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(filepath, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                    for file in os.listdir(temp_dir):
                        if file.lower().endswith(('.xlsx', '.xls', '.csv')):
                            zip_filepath = os.path.join(temp_dir, file)
                            zip_ext = os.path.splitext(zip_filepath)[1].lower()

                            # 在临时目录内读取文件
                            if zip_ext in ('.xlsx', '.xls'):
                                raw = pd.read_excel(zip_filepath, header=None)
                                if len(raw) > 0:
                                    first_row_text = any(
                                        isinstance(raw.iloc[0, c], str) and
                                        any(kw in str(raw.iloc[0, c]) for kw in ['经度', '纬度', 'longitude', 'latitude', '经度（W°）', '纬度（N°）'])
                                        for c in raw.columns
                                    )
                                    if first_row_text:
                                        raw = raw.iloc[1:].reset_index(drop=True)
                            else:
                                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin1']:
                                    try:
                                        raw = pd.read_csv(zip_filepath, header=None, encoding=encoding)
                                        break
                                    except:
                                        continue
                                else:
                                    raise ValueError("无法读取ZIP中的CSV文件")

                            # 成功读取后跳出
                            break
                    else:
                        raise ValueError("ZIP文件中未找到有效的Excel或CSV文件")
        else:
            # 读取文件
            if ext in ('.xlsx', '.xls'):
                raw = pd.read_excel(filepath, header=None)
                if len(raw) > 0:
                    first_row_text = any(
                        isinstance(raw.iloc[0, c], str) and
                        any(kw in str(raw.iloc[0, c]) for kw in ['经度', '纬度', 'longitude', 'latitude', '经度（W°）', '纬度（N°）'])
                        for c in raw.columns
                    )
                    if first_row_text:
                        raw = raw.iloc[1:].reset_index(drop=True)
            else:
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin1']:
                    try:
                        raw = pd.read_csv(filepath, header=None, encoding=encoding)
                        break
                    except:
                        continue
                else:
                    raise ValueError("无法读取CSV文件")

        # 检测经纬度列
        numeric_data = raw.apply(pd.to_numeric, errors='coerce')
        lon_col, lat_col = None, None

        for c in numeric_data.columns:
            col = numeric_data[c].dropna()
            if len(col) < 3:
                continue

            col_name = str(raw.iloc[0, c]) if len(raw) > 0 and pd.notna(raw.iloc[0, c]) else ""
            if col_name in ['经度', 'longitude', 'lon', 'lng', 'X', 'x']:
                lon_col = c
                continue
            elif col_name in ['纬度', 'latitude', 'lat', 'Y', 'y']:
                lat_col = c
                continue

            mean_v = col.mean()
            range_v = col.max() - col.min()
            if 60 <= mean_v <= 160 and range_v < 20 and lon_col is None:
                lon_col = c
            elif 0 <= mean_v <= 60 and range_v < 20 and lat_col is None:
                lat_col = c

        # 回退策略
        if lon_col is None and len(numeric_data.columns) >= 2:
            lon_col = 1
        if lat_col is None and len(numeric_data.columns) >= 3:
            lat_col = 2
        elif lat_col is None and len(numeric_data.columns) >= 2:
            lat_col = 1 if lon_col != 1 else 2

        if lon_col is None or lat_col is None:
            raise ValueError("无法检测到经纬度列")

        lon_roi = numeric_data[lon_col].dropna().values
        lat_roi = numeric_data[lat_col].dropna().values
        roi_poly = np.column_stack([lon_roi, lat_roi])

        return {
            'roi_poly': roi_poly,
            'lon_roi': lon_roi,
            'lat_roi': lat_roi,
            'lon_col': lon_col,
            'lat_col': lat_col
        }
