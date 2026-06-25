# 平台集成文件

本目录是 geo-model3d 与 DeepExplor 平台对接所需的文件（参考副本）。

- `commons/model3d_broker.py` —— 部署时应放到平台的 `commons/` 目录下，
  供下游（geo-reporter 等）通过 `find_model3d_for_bbox` 发现本服务的三维产物。
- geo-reporter 侧消费分支：在 `geo-reporter/reporter/data_sources.py` 增加
  `_geo_model3d_figures(bbox)`（镜像 `_geo_stru_figures`），把三维深度切片/剖面注入报告。
