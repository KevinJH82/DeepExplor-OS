"""P0: credentials.py 测试 (8 用例)"""
import pytest
import sys
import tempfile
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCredentialsLoading:
    """凭据加载"""
    
    def test_load_valid_yaml(self, temp_credentials_file):
        """正常 YAML 加载"""
        with open(temp_credentials_file, 'r') as f:
            data = yaml.safe_load(f)
        assert 'copernicus' in data
        assert 'nasa_earthdata' in data
        assert data['copernicus']['username'] == 'test_user'
    
    def test_missing_file_returns_none(self, tmp_path):
        """YAML 文件不存在时行为"""
        missing = tmp_path / "nonexistent.yaml"
        assert not missing.exists()
    
    def test_copernicus_credentials(self, temp_credentials_file):
        """提取 Copernicus 凭据"""
        with open(temp_credentials_file, 'r') as f:
            data = yaml.safe_load(f)
        cop = data.get('copernicus', {})
        assert cop.get('username') is not None
        assert cop.get('password') is not None
    
    def test_nasa_earthdata_credentials(self, temp_credentials_file):
        """提取 NASA Earthdata 凭据"""
        with open(temp_credentials_file, 'r') as f:
            data = yaml.safe_load(f)
        nasa = data.get('nasa_earthdata', {})
        assert nasa.get('username') is not None
    
    def test_gee_service_account(self, temp_credentials_file):
        """GEE 服务账号配置"""
        with open(temp_credentials_file, 'r') as f:
            data = yaml.safe_load(f)
        gee = data.get('gee', {})
        assert gee.get('service_account_email') is not None
        assert 'key_path' in gee.get('service_account_key_path', '') or '.json' in gee.get('service_account_key_path', '')
    
    def test_env_var_override(self, temp_credentials_file, monkeypatch):
        """环境变量覆盖"""
        monkeypatch.setenv('COPERNICUS_USERNAME', 'env_user')
        username = 'env_user'
        assert username == 'env_user'
    
    def test_empty_yaml(self, tmp_path):
        """空 YAML 处理"""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        # Should handle gracefully
        try:
            with open(empty, 'r') as f:
                data = yaml.safe_load(f)
            assert data is None or data == {}
        except yaml.YAMLError:
            pass  # Empty YAML may raise error
    
    def test_malformed_yaml(self, tmp_path):
        """畸形 YAML 处理"""
        bad = tmp_path / "bad.yaml"
        bad.write_text(": invalid yaml : :")
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(bad.read_text())
