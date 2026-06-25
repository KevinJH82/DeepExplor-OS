"""
Nominatim Geocoding Module
使用 Nominatim API 进行反向地理编码，获取中文行政区划信息。
"""

import urllib.request
import urllib.parse
import json
import ssl
import time
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class LocationContext:
    """地理位置上下文信息"""
    country: str  # 国家名称
    country_code: str  # 国家代码（ISO 3166-1 alpha-2）
    province: str  # 省/州
    city: str  # 城市/市
    district: str  # 区/县
    centroid_lat: float  # 中心点纬度
    centroid_lon: float  # 中心点经度
    min_lon: float  # 最小经度
    min_lat: float  # 最小纬度
    max_lon: float  # 最大经度
    max_lat: float  # 最大纬度
    area_name: str  # KML 中提取的区块名称
    kml_description: str  # KML 中提取的地质背景描述

    @property
    def coords_str(self) -> str:
        """坐标范围字符串：东经XX~XX°，北纬XX~XX°"""
        return f"东经{self.min_lon:.2f}~{self.max_lon:.2f}°，北纬{self.min_lat:.2f}~{self.max_lat:.2f}°"

    @property
    def location_str(self) -> str:
        """位置字符串：省/市/区"""
        parts = [p for p in [self.province, self.city, self.district] if p]
        return " ".join(parts)


class GeocoderError(Exception):
    pass


import re

def _strip_non_cjk(text: str) -> str:
    """保留中文、数字、常用标点，去掉蒙文等其他文字"""
    return re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbf\d\s\-·（）()、，。]', '', text).strip()


def reverse_geocode(lat: float, lon: float, retries: int = 3, timeout: int = 15) -> dict:
    """
    使用 Nominatim 反向地理编码（获取地名）。

    Parameters
    ----------
    lat : float
        纬度
    lon : float
        经度
    retries : int
        重试次数
    timeout : int
        请求超时（秒）

    Returns
    -------
    dict
        包含 address 的字典，其中 address 包含：
        - country, country_code, state, city, town, county, district 等字段
    """
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "accept-language": "zh-CN",
        "zoom": 10
    }

    # 创建 SSL context，允许在握手失败时降级
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(retries):
        try:
            query_string = urllib.parse.urlencode(params)
            full_url = f"{url}?{query_string}"

            req = urllib.request.Request(
                full_url,
                headers={"User-Agent": "geo-reporter/0.1"}
            )

            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data

        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = 2 ** attempt
                if attempt < retries - 1:
                    time.sleep(wait_time)
                    continue
            raise GeocoderError(f"Nominatim HTTP {e.code}: {e.reason}")

        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise GeocoderError(f"Nominatim URL error: {e}")

        except (json.JSONDecodeError, KeyError) as e:
            raise GeocoderError(f"Nominatim response parse error: {e}")

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise GeocoderError(f"Nominatim request failed: {e}")

    raise GeocoderError(f"Failed after {retries} retries")


def create_location_context(
    bbox: Tuple[float, float, float, float],
    area_name: str,
    kml_description: str
) -> LocationContext:
    """
    从 BBox 和 KML 元数据创建地理位置上下文。

    Parameters
    ----------
    bbox : (min_lon, min_lat, max_lon, max_lat)
        边界框坐标
    area_name : str
        KML 中提取的区块名称
    kml_description : str
        KML 中提取的地质背景描述

    Returns
    -------
    LocationContext
        地理位置上下文对象
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    # 计算中心点
    centroid_lat = (min_lat + max_lat) / 2
    centroid_lon = (min_lon + max_lon) / 2

    # 反向地理编码（允许降级：网络不通时使用坐标作为默认信息）
    try:
        geo_data = reverse_geocode(centroid_lat, centroid_lon)
    except GeocoderError:
        geo_data = None

    if geo_data is None:
        country = "未知"
        country_code = ""
        province = ""
        city = ""
        district = ""
    else:
        address = geo_data.get("address", {})

        # 提取行政区划信息
        country = _strip_non_cjk(address.get("country", "未知")) or "未知"
        country_code = address.get("country_code", "").lower()

        # 中国特有的行政区划层级
        province = _strip_non_cjk(address.get("state", ""))
        city = _strip_non_cjk(address.get("city", "") or address.get("town", ""))
        district = _strip_non_cjk(address.get("county", "") or address.get("district", ""))

        # 如果没有获取到城市，尝试用其他字段
        if not city:
            for key in ["state_district", "region"]:
                if key in address:
                    city = _strip_non_cjk(address[key])
                    break

    return LocationContext(
        country=country,
        country_code=country_code,
        province=province,
        city=city,
        district=district,
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        area_name=area_name,
        kml_description=kml_description
    )
