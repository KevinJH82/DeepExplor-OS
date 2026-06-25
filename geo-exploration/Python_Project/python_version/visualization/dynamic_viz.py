"""
动态可视化器

提供交互式可视化功能
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple, List, Union
from loguru import logger


class DynamicVisualizer:
    """
    动态可视化器类

    提供交互式可视化功能（需要额外依赖）
    """

    def __init__(self):
        """初始化动态可视化器"""
        self.plotly_available = self._check_plotly()
        self.folium_available = self._check_folium()

    def _check_plotly(self) -> bool:
        """检查 plotly 是否可用"""
        try:
            import plotly
            return True
        except ImportError:
            logger.warning("plotly 未安装，交互式图表功能不可用")
            return False

    def _check_folium(self) -> bool:
        """检查 folium 是否可用"""
        try:
            import folium
            return True
        except ImportError:
            logger.warning("folium 未安装，地图可视化功能不可用")
            return False

    def create_interactive_map(self, data: np.ndarray, lon_grid: np.ndarray,
                              lat_grid: np.ndarray, title: str = 'Interactive Map',
                              output_file: Optional[str] = None) -> Any:
        """
        创建交互式地图

        Args:
            data: 数据数组
            lon_grid: 经度网格
            lat_grid: 纬度网格
            title: 标题
            output_file: 输出文件路径（可选）

        Returns:
            folium.Map 对象
        """
        if not self.folium_available:
            raise RuntimeError("folium 未安装，请运行: pip install folium")

        import folium
        from folium import plugins

        # 计算中心点和缩放级别
        lon_center = (lon_grid.min() + lon_grid.max()) / 2
        lat_center = (lat_grid.min() + lat_grid.max()) / 2

        # 创建地图
        m = folium.Map(
            location=[lat_center, lon_center],
            zoom_start=10,
            tiles='OpenStreetMap'
        )

        # 添加数据图层
        if data.ndim == 2:
            # 2D 数据 - 使用热力图
            self._add_heatmap(m, data, lon_grid, lat_grid)
        else:
            logger.warning("不支持的数组维度")

        # 添加标题
        title_html = f'''
            <h3 align="center" style="font-size:16px"><b>{title}</b></h3>
            '''
        m.get_root().html.add_child(folium.Element(title_html))

        # 保存到文件
        if output_file is not None:
            m.save(output_file)
            logger.info(f"交互式地图已保存: {output_file}")

        return m

    def _add_heatmap(self, m, data: np.ndarray,
                    lon_grid: np.ndarray, lat_grid: np.ndarray):
        """添加热力图图层"""
        from folium import plugins

        # 准备热力图数据
        heat_data = []
        rows, cols = data.shape

        # 下采样以提高性能
        step = max(1, min(rows, cols) // 1000)

        for i in range(0, rows, step):
            for j in range(0, cols, step):
                if not np.isnan(data[i, j]) and data[i, j] > 0:
                    lon = lon_grid[j] if len(lon_grid.shape) == 1 else lon_grid[i, j]
                    lat = lat_grid[i] if len(lat_grid.shape) == 1 else lat_grid[i, j]
                    heat_data.append([lat, lon, float(data[i, j])])

        if heat_data:
            # 添加热力图
            plugins.HeatMap(heat_data, radius=15, blur=25,
                           max_zoom=13, name='Heatmap').add_to(m)

            # 添加图层控制
            folium.LayerControl().add_to(m)

    def create_interactive_plot(self, data: np.ndarray,
                               title: str = 'Interactive Plot',
                               x_label: str = 'X', y_label: str = 'Y',
                               output_file: Optional[str] = None) -> Any:
        """
        创建交互式图表

        Args:
            data: 数据数组
            title: 标题
            x_label: x 轴标签
            y_label: y 轴标签
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        if data.ndim == 1:
            # 1D 数据 - 折线图
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                y=data,
                mode='lines',
                name='Data'
            ))

        elif data.ndim == 2:
            # 2D 数据 - 热力图
            fig = go.Figure(data=go.Heatmap(
                z=data,
                colorscale='Viridis'
            ))

        else:
            raise ValueError(f"不支持的数组维度: {data.ndim}")

        fig.update_layout(
            title=title,
            xaxis_title=x_label,
            yaxis_title=y_label
        )

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"交互式图表已保存: {output_file}")

        return fig

    def create_3d_surface(self, data: np.ndarray,
                         lon_grid: np.ndarray, lat_grid: np.ndarray,
                         title: str = '3D Surface',
                         output_file: Optional[str] = None) -> Any:
        """
        创建 3D 表面图

        Args:
            data: 数据数组
            lon_grid: 经度网格
            lat_grid: 纬度网格
            title: 标题
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go

        # 创建 3D 表面图
        fig = go.Figure(data=[go.Surface(
            z=data,
            x=lon_grid,
            y=lat_grid,
            colorscale='Viridis',
            colorbar=dict(title='Value')
        )])

        fig.update_layout(
            title=title,
            scene=dict(
                xaxis_title='Longitude',
                yaxis_title='Latitude',
                zaxis_title='Value'
            ),
            autosize=True
        )

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"3D 表面图已保存: {output_file}")

        return fig

    def create_scatter_3d(self, x: np.ndarray, y: np.ndarray, z: np.ndarray,
                         title: str = '3D Scatter Plot',
                         output_file: Optional[str] = None) -> Any:
        """
        创建 3D 散点图

        Args:
            x: x 坐标
            y: y 坐标
            z: z 坐标
            title: 标题
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go

        fig = go.Figure(data=[go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode='markers',
            marker=dict(
                size=3,
                color=z,
                colorscale='Viridis',
                colorbar=dict(title='Value')
            )
        )])

        fig.update_layout(
            title=title,
            scene=dict(
                xaxis_title='X',
                yaxis_title='Y',
                zaxis_title='Z'
            )
        )

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"3D 散点图已保存: {output_file}")

        return fig

    def create_dashboard(self, results: Dict[str, np.ndarray],
                        lon_grid: np.ndarray, lat_grid: np.ndarray,
                        title: str = 'Analysis Dashboard',
                        output_file: Optional[str] = None) -> Any:
        """
        创建交互式仪表板

        Args:
            results: 结果字典
            lon_grid: 经度网格
            lat_grid: 纬度网格
            title: 标题
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # 计算子图数量
        num_plots = len(results)
        cols = min(2, num_plots)
        rows = (num_plots + cols - 1) // cols

        # 创建子图
        fig = make_subplots(
            rows=rows, cols=cols,
            subplot_titles=list(results.keys()),
            specs=[[{'type': 'heatmap'} for _ in range(cols)] for _ in range(rows)]
        )

        # 添加每个数据
        for idx, (name, data) in enumerate(results.items()):
            row, col = idx // cols + 1, idx % cols + 1

            fig.add_trace(
                go.Heatmap(
                    z=data,
                    colorscale='Viridis',
                    showscale=True if idx == 0 else False
                ),
                row=row, col=col
            )

        fig.update_layout(
            title_text=title,
            height=300 * rows,
            showlegend=False
        )

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"交互式仪表板已保存: {output_file}")

        return fig

    def create_comparison_plot(self, data1: np.ndarray, data2: np.ndarray,
                              title: str = 'Comparison Plot',
                              name1: str = 'Data 1', name2: str = 'Data 2',
                              output_file: Optional[str] = None) -> Any:
        """
        创建对比图

        Args:
            data1: 第一个数据
            data2: 第二个数据
            title: 标题
            name1: 第一个名称
            name2: 第二个名称
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        if data1.ndim == 1:
            # 1D 数据对比
            fig = go.Figure()
            fig.add_trace(go.Scatter(y=data1, name=name1, mode='lines'))
            fig.add_trace(go.Scatter(y=data2, name=name2, mode='lines'))

        elif data1.ndim == 2:
            # 2D 数据对比
            fig = make_subplots(
                rows=1, cols=2,
                subplot_titles=[name1, name2],
                specs=[[{'type': 'heatmap'}, {'type': 'heatmap'}]]
            )

            fig.add_trace(go.Heatmap(z=data1, colorscale='Viridis'), row=1, col=1)
            fig.add_trace(go.Heatmap(z=data2, colorscale='Viridis'), row=1, col=2)

        else:
            raise ValueError(f"不支持的数组维度: {data1.ndim}")

        fig.update_layout(title_text=title)

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"对比图已保存: {output_file}")

        return fig

    def create_animation(self, data_series: List[np.ndarray],
                        lon_grid: np.ndarray, lat_grid: np.ndarray,
                        title: str = 'Animation',
                        frame_duration: int = 500,
                        output_file: Optional[str] = None) -> Any:
        """
        创建动画

        Args:
            data_series: 数据序列
            lon_grid: 经度网格
            lat_grid: 纬度网格
            title: 标题
            frame_duration: 每帧持续时间（毫秒）
            output_file: 输出文件路径（可选）

        Returns:
            plotly Figure 对象
        """
        if not self.plotly_available:
            raise RuntimeError("plotly 未安装，请运行: pip install plotly")

        import plotly.graph_objects as go

        # 创建帧
        frames = []
        for i, data in enumerate(data_series):
            frame = go.Frame(
                data=[go.Heatmap(z=data, colorscale='Viridis')],
                name=f'Frame {i}'
            )
            frames.append(frame)

        # 创建初始图
        fig = go.Figure(
            data=[go.Heatmap(z=data_series[0], colorscale='Viridis')],
            frames=frames
        )

        # 添加播放按钮
        fig.update_layout(
            title=title,
            updatemenus=[dict(
                type='buttons',
                showactive=False,
                buttons=[dict(
                    label='Play',
                    method='animate',
                    args=[None, dict(frame=dict(duration=frame_duration, redraw=True),
                                   fromcurrent=True)]
                ), dict(
                    label='Pause',
                    method='animate',
                    args=[[None], dict(frame=dict(duration=0, redraw=False),
                                     mode='immediate')]
                )]
            )]
        )

        # 保存到文件
        if output_file is not None:
            fig.write_html(output_file)
            logger.info(f"动画已保存: {output_file}")

        return fig

    def get_available_features(self) -> Dict[str, bool]:
        """获取可用的功能"""
        return {
            'plotly': self.plotly_available,
            'folium': self.folium_available
        }
