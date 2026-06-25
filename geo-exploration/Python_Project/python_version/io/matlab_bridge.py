"""
MATLAB 兼容层

提供与 MATLAB 数据格式和函数的兼容接口
"""

import numpy as np
from typing import Dict, Any, Optional, Tuple, List, Union
from scipy.io import loadmat, savemat
from scipy.ndimage import gaussian_filter
from loguru import logger


class MatlabBridge:
    """
    MATLAB 兼容桥接类

    提供与 MATLAB 数据格式和常用函数的兼容接口
    """

    # MATLAB 数据类型映射
    TYPE_MAP = {
        'double': np.float64,
        'single': np.float32,
        'int32': np.int32,
        'uint32': np.uint32,
        'int16': np.int16,
        'uint16': np.uint16,
        'int8': np.int8,
        'uint8': np.uint8,
        'logical': np.bool_,
        'char': np.str_,
    }

    @staticmethod
    def load_mat(file_path: str, squeeze_me: bool = True) -> Dict[str, Any]:
        """
        加载 MATLAB .mat 文件

        Args:
            file_path: MAT 文件路径
            squeeze_me: 是否压缩单元素维度

        Returns:
            数据字典
        """
        try:
            data = loadmat(file_path, squeeze_me=squeeze_me)

            # 移除 MATLAB 元数据
            metadata_keys = ['__header__', '__version__', '__globals__']
            for key in metadata_keys:
                data.pop(key, None)

            logger.info(f"已加载 MAT 文件: {file_path}，包含 {len(data)} 个变量")
            return data

        except Exception as e:
            logger.error(f"加载 MAT 文件失败: {str(e)}")
            raise

    @staticmethod
    def save_mat(file_path: str, data: Dict[str, Any],
                 format: str = '4', oned_as: str = 'column'):
        """
        保存为 MATLAB .mat 文件

        Args:
            file_path: MAT 文件路径
            data: 数据字典
            format: MAT 文件版本 ('4' 或 '5')
            oned_as: 一维数组存储方式 ('column' 或 'row')
        """
        try:
            # 转换数据类型
            matlab_data = {}
            for key, value in data.items():
                matlab_data[key] = MatlabBridge._to_matlab_type(value)

            # 保存
            savemat(file_path, matlab_data, format=format, oned_as=oned_as)

            logger.info(f"已保存 MAT 文件: {file_path}")

        except Exception as e:
            logger.error(f"保存 MAT 文件失败: {str(e)}")
            raise

    @staticmethod
    def _to_matlab_type(obj: Any) -> Any:
        """转换为 MATLAB 兼容类型"""
        if isinstance(obj, np.ndarray):
            # 确保是数值类型
            if np.issubdtype(obj.dtype, np.integer):
                return obj.astype(np.int32)
            elif np.issubdtype(obj.dtype, np.floating):
                return obj.astype(np.float64)
            elif np.issubdtype(obj.dtype, np.bool_):
                return obj.astype(np.uint8)
            else:
                return obj
        elif isinstance(obj, (list, tuple)):
            return np.array(obj)
        elif isinstance(obj, (int, float)):
            return np.array([obj])
        elif isinstance(obj, bool):
            return np.array([1 if obj else 0], dtype=np.uint8)
        else:
            return obj

    @staticmethod
    def imgaussfilt(image: np.ndarray, sigma: float = 0.5,
                   padding: str = 'replicate') -> np.ndarray:
        """
        MATLAB imgaussfilt 的 Python 实现

        Args:
            image: 输入图像
            sigma: 高斯标准差
            padding: 边界填充方式

        Returns:
            滤波后的图像
        """
        # 创建高斯核
        kernel_size = int(6 * sigma + 1)
        if kernel_size % 2 == 0:
            kernel_size += 1

        # 设置 padding 模式
        mode_map = {
            'replicate': 'edge',
            'symmetric': 'reflect',
            'circular': 'wrap',
            'zeros': 'constant'
        }
        mode = mode_map.get(padding, 'edge')

        # 执行高斯滤波
        if len(image.shape) == 2:
            # 2D 图像
            result = gaussian_filter(image, sigma=sigma, mode=mode)
        elif len(image.shape) == 3:
            # 3D 数据（多波段图像）
            result = np.zeros_like(image)
            for i in range(image.shape[0]):
                result[i] = gaussian_filter(image[i], sigma=sigma, mode=mode)
        else:
            raise ValueError(f"不支持的图像维度: {len(image.shape)}")

        return result

    @staticmethod
    def imfilter(image: np.ndarray, kernel: np.ndarray,
                mode: str = 'conv', padding: str = 'replicate') -> np.ndarray:
        """
        MATLAB imfilter 的 Python 实现

        Args:
            image: 输入图像
            kernel: 滤波核
            mode: 模式 ('conv' 或 'corr')
            padding: 边界填充方式

        Returns:
            滤波后的图像
        """
        from scipy.ndimage import convolve, correlate

        # 设置 padding 模式
        padding_mode_map = {
            'replicate': 'edge',
            'symmetric': 'reflect',
            'circular': 'wrap',
            'zeros': 'constant'
        }
        pad_mode = padding_mode_map.get(padding, 'edge')

        # 执行滤波
        if mode == 'conv':
            result = convolve(image, kernel, mode=pad_mode)
        else:
            result = correlate(image, kernel, mode=pad_mode)

        return result

    @staticmethod
    def mat2gray(image: np.ndarray,
                min_val: Optional[float] = None,
                max_val: Optional[float] = None) -> np.ndarray:
        """
        MATLAB mat2gray 的 Python 实现

        将图像归一化到 [0, 1] 范围

        Args:
            image: 输入图像
            min_val: 最小值（可选）
            max_val: 最大值（可选）

        Returns:
            归一化后的图像
        """
        # 处理 NaN
        valid_mask = ~np.isnan(image)

        if not np.any(valid_mask):
            return np.zeros_like(image)

        # 确定最小值和最大值
        if min_val is None:
            min_val = np.min(image[valid_mask])
        if max_val is None:
            max_val = np.max(image[valid_mask])

        # 计算范围
        range_val = max_val - min_val

        if range_val == 0:
            return np.zeros_like(image)

        # 归一化
        result = (image - min_val) / range_val

        # 裁剪到 [0, 1]
        result = np.clip(result, 0, 1)

        return result

    @staticmethod
    def imresize(image: np.ndarray, scale: Union[float, Tuple[int, int]],
                method: str = 'bilinear') -> np.ndarray:
        """
        MATLAB imresize 的 Python 实现

        Args:
            image: 输入图像
            scale: 缩放比例或目标尺寸
            method: 插值方法

        Returns:
            调整大小后的图像
        """
        from scipy.ndimage import zoom, map_coordinates

        # 确定插值顺序
        order_map = {
            'nearest': 0,
            'bilinear': 1,
            'bicubic': 3,
            'lanczos': 5
        }
        order = order_map.get(method, 1)

        # 计算缩放因子
        if isinstance(scale, (tuple, list)):
            # 目标尺寸
            if len(image.shape) == 2:
                scale = (scale[0] / image.shape[0],
                        scale[1] / image.shape[1])
            else:
                scale = (scale[0] / image.shape[0],
                        scale[1] / image.shape[1],
                        1)  # 不缩放波段
        elif isinstance(scale, (int, float)):
            # 缩放比例
            if len(image.shape) == 2:
                scale = (scale, scale)
            else:
                scale = (scale, scale, 1)

        # 执行缩放
        if order == 0:
            # 最近邻插值
            if len(image.shape) == 2:
                from scipy.ndimage import zoom
                result = zoom(image, scale, order=order)
            else:
                # 多波段图像
                result = np.zeros((
                    int(image.shape[0] * scale[2]),
                    int(image.shape[1] * scale[0]),
                    int(image.shape[2] * scale[1])
                ), dtype=image.dtype)
                for i in range(image.shape[0]):
                    result[i] = zoom(image[i], scale[:2], order=order)
        else:
            # 其他插值方法
            if len(image.shape) == 2:
                result = zoom(image, scale, order=order)
            else:
                # 多波段图像
                result = np.zeros((
                    image.shape[0],
                    int(image.shape[1] * scale[0]),
                    int(image.shape[2] * scale[1])
                ), dtype=image.dtype)
                for i in range(image.shape[0]):
                    result[i] = zoom(image[i], scale[:2], order=order)

        return result

    @staticmethod
    def polyfit(x: np.ndarray, y: np.ndarray, deg: int) -> np.ndarray:
        """
        MATLAB polyfit 的 Python 实现

        Args:
            x: x 坐标
            y: y 坐标
            deg: 多项式次数

        Returns:
            多项式系数（从高次到低次）
        """
        # 处理 NaN
        valid_mask = ~np.isnan(x) & ~np.isnan(y)
        x_valid = x[valid_mask]
        y_valid = y[valid_mask]

        if len(x_valid) < deg + 1:
            logger.warning(f"数据点不足: {len(x_valid)} < {deg + 1}")

        # 多项式拟合
        coeffs = np.polyfit(x_valid, y_valid, deg)

        return coeffs

    @staticmethod
    def polyval(coeffs: np.ndarray, x: np.ndarray) -> np.ndarray:
        """
        MATLAB polyval 的 Python 实现

        Args:
            coeffs: 多项式系数（从高次到低次）
            x: x 坐标

        Returns:
            y 值
        """
        return np.polyval(coeffs, x)

    @staticmethod
    def meshgrid(*args: np.ndarray, indexing: str = 'xy') -> Tuple[np.ndarray, ...]:
        """
        MATLAB meshgrid 的 Python 实现（与 numpy.meshgrid 兼容）

        Args:
            *args: 网格坐标
            indexing: 索引方式 ('xy' 或 'ij')

        Returns:
            网格数组
        """
        return np.meshgrid(*args, indexing=indexing)

    @staticmethod
    def ndgrid(*args: np.ndarray) -> Tuple[np.ndarray, ...]:
        """
        MATLAB ndgrid 的 Python 实现

        Args:
            *args: 网格坐标

        Returns:
            网格数组
        """
        return np.meshgrid(*args, indexing='ij')

    @staticmethod
    def find(mask: np.ndarray) -> np.ndarray:
        """
        MATLAB find 的 Python 实现

        Args:
            mask: 布尔掩码

        Returns:
            索引数组
        """
        return np.where(mask)[0]

    @staticmethod
    double(x: Any) -> np.ndarray:
        """MATLAB double 转换"""
        if isinstance(x, np.ndarray):
            return x.astype(np.float64)
        else:
            return np.array(x, dtype=np.float64)

    @staticmethod
    single(x: Any) -> np.ndarray:
        """MATLAB single 转换"""
        if isinstance(x, np.ndarray):
            return x.astype(np.float32)
        else:
            return np.array(x, dtype=np.float32)

    @staticmethod
    uint8(x: Any) -> np.ndarray:
        """MATLAB uint8 转换"""
        if isinstance(x, np.ndarray):
            return x.astype(np.uint8)
        else:
            return np.array(x, dtype=np.uint8)

    @staticmethod
    logical(x: Any) -> np.ndarray:
        """MATLAB logical 转换"""
        if isinstance(x, np.ndarray):
            return x.astype(np.bool_)
        else:
            return np.array(x, dtype=np.bool_)

    @staticmethod
    isnan(x: np.ndarray) -> np.ndarray:
        """MATLAB isnan 的 Python 实现"""
        return np.isnan(x)

    @staticmethod
    isinf(x: np.ndarray) -> np.ndarray:
        """MATLAB isinf 的 Python 实现"""
        return np.isinf(x)

    @staticmethod
    isfinite(x: np.ndarray) -> np.ndarray:
        """MATLAB isfinite 的 Python 实现"""
        return np.isfinite(x)

    @staticmethod
    zeros(*args: Union[int, Tuple[int, ...]], dtype: type = np.float64) -> np.ndarray:
        """MATLAB zeros 的 Python 实现"""
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            shape = args[0]
        else:
            shape = args
        return np.zeros(shape, dtype=dtype)

    @staticmethod
    ones(*args: Union[int, Tuple[int, ...]], dtype: type = np.float64) -> np.ndarray:
        """MATLAB ones 的 Python 实现"""
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            shape = args[0]
        else:
            shape = args
        return np.ones(shape, dtype=dtype)

    @staticmethod
    nan(*args: Union[int, Tuple[int, ...]]) -> np.ndarray:
        """MATLAB NaN 数组的 Python 实现"""
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            shape = args[0]
        else:
            shape = args
        return np.full(shape, np.nan)

    @staticmethod
    inf(*args: Union[int, Tuple[int, ...]]) -> np.ndarray:
        """MATLAB Inf 数组的 Python 实现"""
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            shape = args[0]
        else:
            shape = args
        return np.full(shape, np.inf)

    @staticmethod
    sum(x: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
        """MATLAB sum 的 Python 实现"""
        if axis is None:
            return np.sum(x)
        else:
            return np.sum(x, axis=axis)

    @staticmethod
    mean(x: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
        """MATLAB mean 的 Python 实现"""
        if axis is None:
            return np.mean(x)
        else:
            return np.mean(x, axis=axis)

    @staticmethod
    std(x: np.ndarray, axis: Optional[int] = None, ddof: int = 0) -> np.ndarray:
        """MATLAB std 的 Python 实现"""
        if axis is None:
            return np.std(x, ddof=ddof)
        else:
            return np.std(x, axis=axis, ddof=ddof)

    @staticmethod
    size(x: np.ndarray, dim: Optional[int] = None) -> Union[int, Tuple[int, ...]]:
        """MATLAB size 的 Python 实现"""
        if dim is not None:
            return x.shape[dim - 1]  # MATLAB 使用 1-based 索引
        else:
            return x.shape

    @staticmethod
    length(x: np.ndarray) -> int:
        """MATLAB length 的 Python 实现"""
        return max(x.shape)

    @staticmethod
    numel(x: np.ndarray) -> int:
        """MATLAB numel 的 Python 实现"""
        return x.size

    @staticmethod
    linspace(start: float, stop: float, num: int = 50) -> np.ndarray:
        """MATLAB linspace 的 Python 实现"""
        return np.linspace(start, stop, num)

    @staticmethod
    logspace(start: float, stop: float, num: int = 50, base: float = 10) -> np.ndarray:
        """MATLAB logspace 的 Python 实现"""
        return base ** np.linspace(start, stop, num)

    @staticmethod
    cat(dim: int, *args: np.ndarray) -> np.ndarray:
        """MATLAB cat 的 Python 实现"""
        # MATLAB 使用 1-based 索引，Python 使用 0-based
        axis = dim - 1
        return np.concatenate(args, axis=axis)

    @staticmethod
    reshape(x: np.ndarray, *args: Union[int, Tuple[int, ...]]) -> np.ndarray:
        """MATLAB reshape 的 Python 实现"""
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            shape = args[0]
        else:
            shape = args
        return np.reshape(x, shape)

    @staticmethod
    permute(x: np.ndarray, order: Tuple[int, ...]) -> np.ndarray:
        """MATLAB permute 的 Python 实现"""
        # MATLAB 使用 1-based 索引
        order_py = tuple(i - 1 for i in order)
        return np.transpose(x, order_py)

    @staticmethod
    flipdim(x: np.ndarray, dim: int) -> np.ndarray:
        """MATLAB flipdim 的 Python 实现"""
        axis = dim - 1
        if x.ndim == 1:
            return np.flip(x)
        else:
            return np.flip(x, axis=axis)

    @staticmethod
    fliplr(x: np.ndarray) -> np.ndarray:
        """MATLAB fliplr 的 Python 实现"""
        return np.fliplr(x)

    @staticmethod
    flipud(x: np.ndarray) -> np.ndarray:
        """MATLAB flipud 的 Python 实现"""
        return np.flipud(x)

    @staticmethod
    rot90(x: np.ndarray, k: int = 1) -> np.ndarray:
        """MATLAB rot90 的 Python 实现"""
        return np.rot90(x, k)

    @staticmethod
    repmat(x: np.ndarray, m: int, n: int) -> np.ndarray:
        """MATLAB repmat 的 Python 实现"""
        return np.tile(x, (m, n))

    @staticmethod
    unique(x: np.ndarray, return_index: bool = False,
           return_inverse: bool = False) -> Union[np.ndarray, Tuple]:
        """MATLAB unique 的 Python 实现"""
        return np.unique(x, return_index=return_index,
                       return_inverse=return_inverse)

    @staticmethod
    sort(x: np.ndarray, axis: int = -1, kind: str = 'quicksort') -> np.ndarray:
        """MATLAB sort 的 Python 实现"""
        return np.sort(x, axis=axis, kind=kind)

    @staticmethod
def argsort(x: np.ndarray, axis: int = -1, kind: str = 'quicksort') -> np.ndarray:
        """MATLAB argsort 的 Python 实现"""
        return np.argsort(x, axis=axis, kind=kind)


# 创建便捷访问的模块级函数
load_mat = MatlabBridge.load_mat
save_mat = MatlabBridge.save_mat
imgaussfilt = MatlabBridge.imgaussfilt
mat2gray = MatlabBridge.mat2gray
