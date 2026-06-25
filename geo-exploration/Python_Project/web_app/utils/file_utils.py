"""
文件处理工具类
"""

import os
import re
import shutil
import zipfile
import tempfile
from datetime import datetime
import hashlib
import pandas as pd

from config.config import Config


def make_safe_filename(filename):
    """安全化文件名：剥离路径成分与危险字符，保留中文等 Unicode 字符及扩展名。"""
    name = os.path.basename(filename).replace('\x00', '')
    name = re.sub(r'[/\\:*?"<>|]', '_', name).strip().lstrip('.')
    if not name:
        name = 'upload_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    return name


def save_uploaded_file(file, filename, upload_type):
    """
    保存上传的文件

    Args:
        file: 文件对象
        filename: 文件名
        upload_type: 上传类型 (data_dir, roi_file, kml_file 等)

    Returns:
        str: 保存的文件路径
    """
    # 上传目录：使用 Config 中的绝对路径，不依赖运行时 CWD
    upload_dir = os.path.join(Config.UPLOAD_FOLDER, upload_type)
    os.makedirs(upload_dir, exist_ok=True)

    safe_filename = make_safe_filename(filename)

    if upload_type == 'data_dir' and filename.lower().endswith('.zip'):
        # 数据目录以 zip 上传：先存到临时目录，再解压到上传目录
        temp_dir = os.path.join(Config.TEMP_FOLDER,
                                f'data_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        os.makedirs(temp_dir, exist_ok=True)

        zip_path = os.path.join(temp_dir, safe_filename)
        file.save(zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(upload_dir)

        # 返回解压后的目录
        return upload_dir

    # 其他类型文件（roi_file / kml_file 等）
    file_path = os.path.join(upload_dir, safe_filename)
    file.save(file_path)
    return file_path


def get_file_size(file_path):
    """
    获取文件大小（格式化显示）

    Args:
        file_path: 文件路径

    Returns:
        str: 格式化后的文件大小
    """
    if not os.path.exists(file_path):
        return "0 B"

    size = os.path.getsize(file_path)

    # 格式化文件大小
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0

    return f"{size:.2f} TB"


def calculate_file_hash(file_path, algorithm='md5'):
    """
    计算文件哈希值

    Args:
        file_path: 文件路径
        algorithm: 哈希算法 (md5, sha1, sha256)

    Returns:
        str: 哈希值
    """
    hash_func = hashlib.new(algorithm)

    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_func.update(chunk)

    return hash_func.hexdigest()


def create_result_zip(result_dir, zip_filename):
    """
    创建结果压缩包

    Args:
        result_dir: 结果目录
        zip_filename: 压缩包文件名

    Returns:
        str: 压缩包路径
    """
    zip_path = os.path.join('results', zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(result_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, result_dir)
                zipf.write(file_path, arcname)

    return zip_path


def validate_excel_file(file_path):
    """
    验证 Excel 文件格式

    Args:
        file_path: 文件路径

    Returns:
        tuple: (是否有效, 错误信息)
    """
    try:
        pd.read_excel(file_path, nrows=1)
        return True, None
    except Exception as e:
        return False, str(e)


def validate_kml_file(file_path):
    """
    验证 KML/KMZ 文件格式

    Args:
        file_path: 文件路径

    Returns:
        tuple: (是否有效, 错误信息)
    """
    try:
        with zipfile.ZipFile(file_path) as z:
            # 检查是否有 kml 文件
            kml_files = [f for f in z.namelist() if f.endswith('.kml')]
            if not kml_files:
                return False, "KML 文件中未找到 .kml 文件"
        return True, None
    except Exception as e:
        return False, str(e)


def cleanup_old_files(directory, days=30):
    """
    清理旧文件

    Args:
        directory: 目录路径
        days: 保留天数
    """
    cutoff_time = datetime.now().timestamp() - (days * 24 * 60 * 60)

    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if os.path.getmtime(file_path) < cutoff_time:
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"删除文件失败 {file_path}: {e}")


def get_directory_size(directory):
    """
    获取目录大小

    Args:
        directory: 目录路径

    Returns:
        int: 目录大小（字节）
    """
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size


def format_directory_size(size_bytes):
    """
    格式化目录大小显示

    Args:
        size_bytes: 字节数

    Returns:
        str: 格式化后的大小
    """
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.2f} {size_names[i]}"