# 舒曼波共振遥感矿产预测系统 - Python 版本

基于舒曼波共振理论的遥感矿产预测系统的 Python 完整实现，完全兼容 MATLAB 原版系统的功能。

## 系统概述

本系统通过融合多种遥感数据（Sentinel-2、Landsat-8、ASTER、DEM）和多种探测器算法，实现深部成矿预测。系统采用模块化设计，支持并行计算，并提供完整的可视化功能。

### 主要功能

- **多源遥感数据加载**：支持 Sentinel-2、Landsat-8、ASTER、DEM 等多种遥感数据格式
- **多探测器融合**：集成红边检测、本征吸收检测、慢变量检测、已知异常检测等多种算法
- **深度与压力反演**：基于 Yakymchuk 模型实现深度和压力的反演计算
- **地表潜力计算**：结合 PCA 分析和地表变量计算地表成矿潜力
- **多种可视化输出**：支持静态图像、交互式图表、Google Earth KMZ 文件等多种输出格式

### 支持的矿物类型

- 金 (gold)
- 铜 (copper)
- 铁 (iron)
- 煤 (coal)
- 石油 (petroleum)
- 铅锌 (lead_zinc)
- 锑 (antimony)
- 钨 (tungsten)
- 钼 (molybdenum)
- 镍 (nickel)
- 铬 (chromium)

## 安装

### 环境要求

- Python 3.8+
- GDAL 3.0+ (用于地理数据处理)

### 安装步骤

1. 克隆或下载本项目

```bash
cd python_version
```

2. 创建虚拟环境（推荐）

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

3. 安装依赖

```bash
pip install -r requirements.txt
```

**注意**：如果 GDAL 安装失败，可以使用以下命令：

```bash
# Ubuntu/Debian
sudo apt-get install gdal-bin libgdal-dev

# macOS
brew install gdal

# Windows
# 下载预编译的 GDAL wheel 文件
pip install GDAL-<version>-cp<version>-cp<version>-win_amd64.whl
```

## 快速开始

### 命令行使用

```bash
python main.py \
    --data-dir ./data \
    --roi-file ./roi.xlsx \
    --mineral-type gold \
    --kmz-path ./known_anomalies.kmz \
    --output-dir ./results
```

### Python API 使用

```python
from run_core_algorithm import run_core_algorithm

# 执行分析
output_dir, results = run_core_algorithm(
    data_dir='./data',
    roi_file='./roi.xlsx',
    mineral_type='gold',
    kmz_path='./known_anomalies.kmz',
    kmz_threshold=0.6,
    output_dir='./results'
)

print(f"结果保存至: {output_dir}")
print(f"生成文件: {results['files']}")
```

### 快速分析（仅返回结果数组）

```python
from run_core_algorithm import quick_analysis

# 获取预测结果
prediction = quick_analysis(
    data_dir='./data',
    roi_file='./roi.xlsx',
    mineral_type='gold'
)

print(f"预测结果形状: {prediction.shape}")
```

## 项目结构

```
python_version/
├── config/                 # 配置管理
│   ├── __init__.py
│   └── config.py          # 系统配置
├── core/                   # 核心模块
│   ├── __init__.py
│   ├── base_classes.py     # 基础类
│   ├── fusion_engine.py    # 融合引擎
│   ├── geo_data_context.py # 地理数据上下文
│   └── post_processor.py   # 后处理器
├── detectors/              # 探测器
│   ├── __init__.py
│   ├── base_detector.py    # 探测器基类
│   ├── red_edge_detector.py    # 红边检测器
│   ├── intrinsic_detector.py  # 本征吸收检测器
│   ├── slow_vars_detector.py  # 慢变量检测器
│   └── known_anomaly_detector.py # 已知异常检测器
├── io/                     # 输入输出
│   ├── __init__.py
│   ├── data_loader.py      # 数据加载器
│   ├── result_exporter.py  # 结果导出器
│   └── matlab_bridge.py    # MATLAB 兼容层
├── utils/                  # 工具函数
│   ├── __init__.py
│   ├── geo_utils.py        # 地理工具
│   ├── file_utils.py       # 文件工具
│   └── logger.py           # 日志工具
├── visualization/          # 可视化
│   ├── __init__.py
│   ├── visualizer.py       # 静态可视化
│   ├── kmz_export.py       # KMZ 导出
│   └── dynamic_viz.py      # 动态可视化
├── main.py                 # 主入口
├── run_core_algorithm.py   # 核心算法接口
├── requirements.txt        # 依赖列表
└── README.md              # 本文件
```

## 数据格式

### ROI 文件格式

ROI 文件应为 CSV 或 Excel 格式，包含经纬度列。系统会自动识别经纬度列：

| 列名示例 | 说明 |
|---------|------|
| lon, longitude, 经度 | 经度 |
| lat, latitude, 纬度 | 纬度 |

示例 CSV 格式：

```csv
lon,lat
116.5,39.8
116.6,39.8
116.6,39.9
116.5,39.9
```

### 遥感数据格式

系统支持以下遥感数据格式：

- Sentinel-2: GeoTIFF 格式，文件名包含 S2 或 Sentinel2
- Landsat-8: GeoTIFF 格式，文件名包含 L8 或 Landsat8
- ASTER: GeoTIFF 格式，文件名包含 AST 或 ASTER
- DEM: GeoTIFF 格式，文件名包含 DEM 或 SRTM

### KMZ/KML 文件格式

已知异常文件应为 KMZ 或 KML 格式，包含 Placemark 元素，每个 Placemark 应包含 Point/coordinates。

## API 参考

### 核心算法

#### `run_core_algorithm()`

执行完整的矿产预测分析流程。

**参数：**
- `data_dir` (str): 数据目录路径
- `roi_file` (str): ROI 文件路径
- `mineral_type` (str): 矿物类型
- `kmz_path` (str, 可选): KMZ/KML 文件路径
- `kmz_threshold` (float, 可选): KMZ 导出阈值，默认 0.6
- `fusion_mode` (bool, 可选): 是否使用融合模式，默认 True
- `output_dir` (str, 可选): 输出目录
- `enable_detectors` (dict, 可选): 启用/禁用特定探测器
- `verbose` (bool, 可选): 是否输出详细日志，默认 True

**返回：**
- `output_dir` (str): 输出目录路径
- `result_dict` (dict): 结果字典

### 探测器

#### RedEdgeDetector（红边检测器）

基于 Sentinel-2 红边位置（S2REP）的异常检测。

#### IntrinsicDetector（本征吸收检测器）

基于矿物特征光谱吸收的异常检测。

#### SlowVarsDetector（慢变量检测器）

基于地质构造因素的异常检测。

#### KnownAnomalyDetector（已知异常检测器）

集成已知矿点数据的异常检测。

## 配置

系统配置文件位于 `config/config.py`，可以修改以下参数：

```python
# 算法参数
ALGORITHM = {
    'eps_value': 1e-10,        # 防止除零的小值
    'gaussian_sigma': 4,       # 高斯滤波标准差
    'z_score_threshold': 2.5,  # Z-score 阈值
}

# 遥感数据波段配置
REMOTE_SENSING = {
    'sentinel2_bands': {...},
    'aster_bands': {...},
}

# 探测器配置
DETECTORS = {
    'red_edge': {...},
    'intrinsic': {...},
    'slow_vars': {...},
    'known_anomaly': {...},
}
```

## 输出结果

系统生成以下文件：

| 文件名 | 说明 |
|--------|------|
| mineral_prediction_results.mat | MATLAB 格式结果文件 |
| analysis_config.json | 分析配置和统计信息 |
| 01_共振参数综合图.png | 共振参数可视化 |
| 02_掩码集成.png | 融合掩码可视化 |
| 03_深部成矿预测图.png | 深部预测结果 |
| 04_深度反演图.png | 深度反演结果 |
| 05_压力反演图.png | 压力反演结果 |
| mineral_prediction.kmz | Google Earth 可视化文件 |

## 性能优化

### 并行计算

系统支持并行计算多个探测器，可通过以下方式启用：

```python
# 自动启用（默认）
engine.compute_all(context, parallel=True)

# 禁用并行计算
engine.compute_all(context, parallel=False)
```

### 内存优化

对于大型数据集，建议：

1. 使用较小的 ROI 范围
2. 降低遥感数据分辨率
3. 禁用不必要的探测器

## 故障排除

### GDAL 安装失败

如果 GDAL 安装失败，请参考：

- [GDAL 官方文档](https://gdal.org/)
- [rasterio 安装指南](https://rasterio.readthedocs.io/)

### 内存不足

如果遇到内存不足错误：

1. 减少数据范围
2. 降低处理分辨率
3. 逐个运行探测器而非并行

### 坐标系统问题

如果遇到坐标转换问题：

1. 确保 ROI 文件中的坐标为 WGS84 (EPSG:4326)
2. 检查遥感数据的坐标系统
3. 使用 QGIS 等工具预处理数据

## 开发

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_detectors.py
```

### 代码格式化

```bash
# 使用 black 格式化代码
black .

# 使用 flake8 检查代码
flake8 .
```

## 许可证

本项目仅供学习和研究使用。

## 联系方式

如有问题或建议，请联系项目维护者。

## 致谢

- 原始 MATLAB 系统开发团队
- GDAL 和 rasterio 开发团队
- 科学 Python 生态系统

## 更新日志

### v1.0.0 (2024)

- 初始 Python 版本发布
- 完整的 MATLAB 系统功能移植
- 支持多种遥感数据格式
- 支持多种可视化输出
- 并行计算支持
