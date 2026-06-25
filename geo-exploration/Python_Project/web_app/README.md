# 舒曼波共振遥感矿产预测系统 - Web 版本

## 项目简介

本项目是基于 Flask + Bootstrap 开发的 Web 版矿产预测系统，提供专业的遥感矿产分析界面，支持多种探测器并行计算和可视化展示。

## 功能特性

### 🔬 核心功能
- **多探测器融合分析**：红边、本征吸收、慢变量、已知异常等多种检测器
- **实时进度监控**：分析任务进度实时更新
- **结果可视化**：共振参数、掩码集成、深部预测等多维度展示
- **数据管理**：支持多种格式的地理数据上传和管理

### 🎨 界面特色
- **专业深色主题**：匹配专业 GIS 系统风格
- **响应式布局**：适配不同屏幕尺寸
- **拖拽上传**：直观的文件上传方式
- **实时日志**：详细的任务执行日志

### 🚀 部署方式
- 本地运行
- Docker 容器化
- 服务器部署

## 安装说明

### 方式一：直接安装（推荐开发环境）

1. **克隆项目**
```bash
git clone <项目地址>
cd web_app
```

2. **创建虚拟环境**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **运行应用**
```bash
python run.py
```

访问 http://localhost:8080 即可使用。

### 方式二：Docker 部署（推荐生产环境）

1. **构建镜像**
```bash
docker build -t mineral-analysis .
```

2. **运行容器**
```bash
docker run -p 8080:8080 mineral-analysis
```

### 方式三：使用 Docker Compose（推荐完整部署）

1. **启动所有服务**
```bash
docker-compose up -d
```

2. **查看服务状态**
```bash
docker-compose ps
```

3. **停止服务**
```bash
docker-compose down
```

## 使用指南

### 1. 数据准备

#### 数据文件夹
- 包含遥感数据（Sentinel-2, Landsat-8, ASTER）
- 支持 ZIP 格式的压缩包
- 自动解压到指定目录

#### ROI 坐标文件
- 支持 Excel (.xlsx, .xls) 和 CSV 格式
- 包含经纬度坐标数据
- 自动识别坐标列

#### KML/KMZ 已知异常（可选）
- Google Earth 格式的已知矿点数据
- 支持地理坐标校正
- 与遥感数据自动对齐

### 2. 参数配置

#### 基础参数
- **目标矿种**：选择要分析的矿物类型
- **KMZ 置信度**：设置输出结果的置信度阈值
- **任务名称**：自定义任务名称（可选）

#### 探测器选择
- **RedEdge (红边)**：基于红边位置偏移
- **Intrinsic (本征吸收)**：基于矿物光谱特征
- **SlowVars (慢变量)**：地质构造因素综合
- **KnownAnomaly (KML)**：集成已知异常数据

#### 融合模式
- 开启：叠加地表背景，增强视觉效果
- 关闭：直接输出探测器原始结果

### 3. 执行分析

1. 上传必需的数据文件
2. 选择目标矿种和探测器
3. 点击"开始运行分析"
4. 实时查看进度和日志
5. 分析完成后查看结果

### 4. 结果下载

分析完成后，可以下载完整的结果包，包括：
- 可视化图像
- 数据文件
- KMZ 地理文件

## 目录结构

```
web_app/
├── app.py              # Flask 主应用
├── run.py              # 启动脚本
├── requirements.txt    # 依赖列表
├── Dockerfile         # Docker 配置
├── docker-compose.yml # Docker Compose 配置
├── config/            # 配置目录
│   └── config.py      # 主配置文件
├── core/              # 核心算法
│   └── mineral_engine.py
├── utils/             # 工具函数
│   ├── file_utils.py
│   └── logger.py
├── templates/         # 模板文件
│   ├── base.html
│   └── index.html
├── uploads/           # 上传文件目录
├── results/           # 结果输出目录
└── logs/              # 日志目录
```

## 配置说明

### 服务器配置
```python
# config/config.py
HOST = '0.0.0.0'      # 服务器地址
PORT = 8080           # 端口
DEBUG = False         # 调试模式
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 最大文件大小
```

### 矿物类型配置
支持多种矿物类型，每种都有对应的颜色方案和参数：
```python
MINERAL_TYPES = {
    'gold': {'name': '黄金', 'color': '#FFD700'},
    'copper': {'name': '铜矿', 'color': '#B87333'},
    # ...
}
```

### 探测器配置
每个探测器都有特定的算法参数：
```python
DETECTORS = {
    'red_edge': {
        'name': 'RedEdge (红边)',
        'description': '基于红边位置偏移和 Moran I 空间自相关'
    },
    # ...
}
```

## API 接口

### 文件上传
```
POST /api/upload
参数：
- type: 上传类型 (data_dir, roi_file, kml_file)
- file: 文件
```

### 启动分析
```
POST /api/start_analysis
参数（JSON）：
{
    "mineral_type": "gold",
    "detectors": ["red_edge", "intrinsic"],
    "fusion_mode": true,
    "kmz_threshold": 0.6,
    "task_name": "任务名称"
}
```

### 查询任务状态
```
GET /api/task_status/<task_id>
```

### 下载结果
```
GET /api/download/<task_id>
```

## 常见问题

### 1. 文件上传失败
- 检查文件格式是否支持
- 确保文件大小不超过限制
- 查看浏览器控制台错误信息

### 2. 分析任务失败
- 检查输入数据格式
- 查看运行日志获取错误信息
- 确保有足够的计算资源

### 3. 无法访问界面
- 确认服务已启动
- 检查端口是否被占用
- 查看防火墙设置

### 4. Docker 部署问题
- 确保有足够的磁盘空间
- 检查 Docker 服务是否运行
- 查看 Docker 日志：`docker logs <容器名>`

## 性能优化

### 1. 服务器优化
- 增加内存和 CPU
- 使用 SSD 存储
- 配置负载均衡

### 2. 算法优化
- 启用并行计算
- 优化内存使用
- 使用 GPU 加速

### 3. 网络优化
- 启用 gzip 压缩
- 使用 CDN 加速
- 优化图片大小

## 更新日志

### v1.0.0 (2024-01-01)
- 初始版本发布
- 基础功能实现
- Web 界面完成

## 联系方式

如有问题或建议，请联系：
- 邮箱：your-email@example.com
- 项目地址：https://github.com/your-username/mineral-analysis-web