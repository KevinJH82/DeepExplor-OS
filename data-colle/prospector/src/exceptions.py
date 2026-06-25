"""Prospector 自定义异常体系"""


class ProspectorError(Exception):
    """基础异常"""
    def __init__(self, message: str, step: str = ""):
        super().__init__(message)
        self.step = step


class NetworkError(ProspectorError):
    """网络请求失败（超时、连接错误、HTTP 错误码）"""
    def __init__(self, message: str, url: str = "", status_code: int = 0, step: str = ""):
        super().__init__(message, step)
        self.url = url
        self.status_code = status_code


class DataFormatError(ProspectorError):
    """数据解析或格式异常"""
    pass


class ROIError(ProspectorError):
    """ROI 文件解析异常"""
    pass


class FetchError(ProspectorError):
    """数据采集失败（地质/物探/化探/遥感）"""
    def __init__(self, message: str, source: str = "", partial: bool = False, step: str = ""):
        super().__init__(message, step)
        self.source = source
        self.partial = partial  # 是否有部分结果可用
