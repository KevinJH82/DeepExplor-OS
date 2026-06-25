"""
Credentials Manager
读取 config/credentials.yaml，提供各平台的账号信息。
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class CredentialsError(Exception):
    pass


# 默认配置文件路径（相对于项目根目录）
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "credentials.yaml"


def load_credentials(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载 credentials.yaml。

    优先级：
    1. 命令行指定的 config_path
    2. 环境变量 GEO_DOWNLOADER_CONFIG
    3. 默认路径 config/credentials.yaml

    Returns
    -------
    dict：各平台的账号信息
    """
    if not HAS_YAML:
        raise CredentialsError(
            "缺少依赖: pyyaml\n请运行: pip install pyyaml"
        )

    # 确定配置文件路径
    if config_path:
        path = Path(config_path)
    elif os.environ.get("GEO_DOWNLOADER_CONFIG"):
        path = Path(os.environ["GEO_DOWNLOADER_CONFIG"])
    else:
        path = DEFAULT_CONFIG_PATH

    if not path.exists():
        raise CredentialsError(
            f"配置文件不存在: {path}\n"
            f"请复制 credentials.yaml.example 为 config/credentials.yaml 并填写账号信息。"
        )

    with open(path, 'r', encoding='utf-8') as f:
        try:
            creds = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise CredentialsError(f"credentials.yaml 格式错误: {e}")

    return creds or {}


def get_platform_creds(creds: dict, platform: str) -> Dict[str, str]:
    """
    获取指定平台的账号信息。

    Parameters
    ----------
    platform : str
        平台名称，如 'copernicus', 'usgs', 'nasa_earthdata'
    """
    if platform not in creds:
        raise CredentialsError(
            f"credentials.yaml 中未找到平台 '{platform}' 的配置。\n"
            f"请参考 credentials.yaml.example 补充配置。"
        )

    entry = creds[platform]
    username = entry.get('username') or os.environ.get(f"{platform.upper()}_USERNAME")
    password = entry.get('password') or os.environ.get(f"{platform.upper()}_PASSWORD")

    if not username or not password:
        # 允许只配置 token 的平台（如 EnMAP）
        token = entry.get('token') or os.environ.get(f"{platform.upper()}_TOKEN")
        if token:
            return {"username": username or "", "password": password or "", "token": token}
        # 允许 key-only 类型平台（如 GEE 服务账号、Planet API Key）
        non_empty = {k: v for k, v in entry.items() if v}
        if non_empty:
            return non_empty
        raise CredentialsError(
            f"平台 '{platform}' 的 username 或 password 为空。\n"
            f"也可以通过环境变量 {platform.upper()}_USERNAME / {platform.upper()}_PASSWORD 设置。"
        )

    result = {"username": username, "password": password}
    # 额外字段（如 token）也一并传入
    token = entry.get("token") or os.environ.get(f"{platform.upper()}_TOKEN")
    if token:
        result["token"] = token
    return result
