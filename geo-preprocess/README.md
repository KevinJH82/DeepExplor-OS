# geo-preprocess — 遥感数据预处理子系统

从 geo-analyser 拆分而来,与 `geo-downloader` / `geo-analyser` / `geo-stru` 平级。
专注遥感影像的**数据预处理**:大气校正 → 几何校正 → 干扰剔除。

## 功能

| 模块 | 说明 | 路由 |
|---|---|---|
| 大气校正 | DOS / 简化法,DN→地表反射率(Landsat8/9、Sentinel-2);L2 产品自动跳过 | `/api/process` |
| 几何校正 | 仿射 / 多项式 / 参考图配准(ORB 特征匹配) | `/api/process` |
| 干扰剔除 | NDVI/NDWI/MNDWI/NDBI/NDSI 掩膜:植被/水体/建筑/云/雪 | `/api/process` |
| 目录扫描 | 识别多波段单文件 / 单波段目录(B1.tif…) / ASTER 多分辨率 | `/api/scan` |
| 预览 | RGB 合成缩略图 + 数值统计 | `/api/preview` |
| 目录浏览 | 文件系统目录选择 | `/api/browse` |

**产物**:`<stem>_corrected.tif` + 5 张干扰掩膜(`*_mask_vegetation/water/cloud/snow/buildup.tif`),供 geo-analyser 蚀变分析复用。

## 目录结构

```
geo-preprocess/
├── app.py              # Flask 路由
├── run.py              # 启动脚本(--host/--port/--debug)
├── config/config.py    # 端口等配置(默认 5002)
├── core/               # 核心算法
│   ├── atmospheric_correction.py
│   ├── geometric_correction.py
│   └── interference_removal.py   # 指数函数引自 commons/spectral_indices
├── utils/pipeline.py   # 扫描/读写/预览 + 三步流水线
├── templates/preprocessing.html
└── static/
```

## 启动

```bash
pip install -r requirements.txt
python run.py --port 5002
# 访问 http://127.0.0.1:5002
```

## 与其它系统的关系
- 纯光谱指数函数共享自 `commons/spectral_indices.py`(与 geo-analyser 同源)。
- 预处理产物(corrected.tif + masks)可被 geo-analyser 蚀变分析按需复用。
- 不依赖 geo-analyser;独立运行。
