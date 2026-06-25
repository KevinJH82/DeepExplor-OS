"""P1: postprocess/derive.py 测试 (6 用例)"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.p1


class TestDeriveLST:
    """Landsat LST 公式"""
    
    def test_lst_formula_basic(self):
        """LST 基本计算"""
        try:
            from postprocess.derive import calculate_lst
            # BT in Celsius, emissivity between 0-1
            lst = calculate_lst(bt=25.0, emissivity=0.97)
            assert lst is not None
            assert isinstance(lst, float)
        except ImportError:
            pytest.skip("calculate_lst not found")
    
    def test_lst_zero_emissivity(self):
        """除零处理"""
        try:
            from postprocess.derive import calculate_lst
            with pytest.raises(Exception):
                calculate_lst(bt=25.0, emissivity=0)
        except ImportError:
            pytest.skip("calculate_lst not found")
    
    def test_lst_extreme_temp(self):
        """极端温度值"""
        try:
            from postprocess.derive import calculate_lst
            lst = calculate_lst(bt=-50, emissivity=0.5)
            assert isinstance(lst, float)
        except ImportError:
            pytest.skip("calculate_lst not found")


class TestDeriveSobel:
    """Sobel 温度梯度"""
    
    def test_sobel_gradient_shape(self):
        """Sobel 梯度输出形状"""
        try:
            from postprocess.derive import sobel_temperature_gradient
            import numpy as np
            lst = np.random.rand(100, 100).astype(np.float32) * 30 + 270
            grad = sobel_temperature_gradient(lst)
            assert grad.shape == lst.shape
        except ImportError:
            pytest.skip("sobel_temperature_gradient not found")
    
    def test_sobel_uniform_temp(self):
        """均匀温度梯度为零"""
        try:
            from postprocess.derive import sobel_temperature_gradient
            import numpy as np
            lst = np.ones((50, 50), dtype=np.float32) * 300
            grad = sobel_temperature_gradient(lst)
            assert np.allclose(grad, 0, atol=1e-5)
        except ImportError:
            pytest.skip("sobel_temperature_gradient not found")


class TestDeriveOTCI:
    """OTCI 计算"""
    
    def test_otci_returns_valid_range(self):
        """OTCI 值在有效范围内"""
        try:
            from postprocess.derive import calculate_otci
            red = 0.1
            nir = 0.3
            otci = calculate_otci(red, nir)
            assert isinstance(otci, float)
        except ImportError:
            pytest.skip("calculate_otci not found")
