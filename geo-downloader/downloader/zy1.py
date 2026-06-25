"""
ZY-1 02D Hyperspectral Downloader — 资源一号02D（中国资源卫星应用中心）
中国高光谱卫星，AHSI传感器，166波段，400-2500nm，分辨率30m

数据申请（免费，需注册国内账号）：
  https://data.cresda.cn/  （中国资源卫星应用中心数据服务平台）

产品：ZY-1 02D AHSI L2 地表反射率
格式：GeoTIFF

注意：
  CRESDA 数据门户目前无公开 REST API，所有数据申请和下载均需：
  1. 在 https://data.cresda.cn/ 注册账号（需要中国大陆手机号）
  2. 在门户搜索感兴趣区域和时间范围
  3. 提交数据申请单（审批通常1-3个工作日）
  4. 审批通过后从"我的订单"下载
  本下载器为占位实现，仅提供操作指引，不执行自动下载。
"""

from pathlib import Path
from typing import List, Tuple, Dict, Any


class ZY1Downloader:
    """ZY-1 02D 占位下载器——提供手动下载指引"""

    PLATFORM_NAME = "zy1"
    REQUIRES_AUTH = False   # 无可用 API，无需在此处认证

    def __init__(self, credentials: Dict[str, str] = None,
                 output_dir: str = "./downloads", **kwargs):
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # 公开接口（与其他下载器保持一致，均返回空列表）
    # ------------------------------------------------------------------

    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        cloud_cover: int = 80,
        **kwargs,
    ) -> List[Dict]:
        min_lon, min_lat, max_lon, max_lat = bbox
        self._print_instructions(min_lon, min_lat, max_lon, max_lat, start_date, end_date)
        return []

    def download(
        self,
        search_results: List[Dict],
        save_dir: Path,
        max_items: int = 3,
        **kwargs,
    ) -> List[Path]:
        return []

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _print_instructions(self, min_lon, min_lat, max_lon, max_lat,
                            start_date, end_date):
        print("  [ZY-1 02D] 资源一号02D 暂无公开 REST API，请按以下步骤手动申请数据：")
        print()
        print("  ① 注册/登录 CRESDA 数据服务平台")
        print("     https://data.cresda.cn/")
        print()
        print('  ② 进入"数据搜索"，选择传感器：ZY-1 02D / AHSI')
        print(f"     范围：E{min_lon:.4f}~{max_lon:.4f}, N{min_lat:.4f}~{max_lat:.4f}")
        print(f"     时间：{start_date} ~ {end_date}")
        print("     产品级别：L2（大气校正）")
        print()
        print("  ③ 将满足条件的场景加入购物车，提交申请单")
        print("     审批通常需要 1-3 个工作日")
        print()
        print('  ④ 审批通过后在"我的订单"中下载，将 .tif 文件放入对应区域目录')
        print()
        print("  官方文档：http://www.cresda.com/CN/satellite/9079.shtml")
        print()
