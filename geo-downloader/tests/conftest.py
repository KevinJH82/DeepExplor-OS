"""
geo-downloader 回归测试 — conftest.py
生成测试 fixtures、mock 工具、共享配置
"""
import os
import sys
import pytest
import tempfile
from pathlib import Path

# Add downloader to path
sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ═══════════════════════════════════════
# KML Fixtures
# ═══════════════════════════════════════

@pytest.fixture
def sample_polygon_kml():
    """简单多边形 KML"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
    <name>Test Zone</name>
    <Placemark>
        <name>Test Polygon</name>
        <Polygon>
            <outerBoundaryIs>
                <LinearRing>
                    <coordinates>
                        -3.68,12.50,0 -3.61,12.50,0 -3.61,12.54,0 -3.68,12.54,0 -3.68,12.50,0
                    </coordinates>
                </LinearRing>
            </outerBoundaryIs>
        </Polygon>
    </Placemark>
</Document>
</kml>"""

@pytest.fixture
def sample_kml_file(sample_polygon_kml, tmp_path):
    """写入临时文件的 KML"""
    f = tmp_path / "test_polygon.kml"
    f.write_text(sample_polygon_kml)
    return str(f)

@pytest.fixture
def sample_point_kml():
    """单点 KML"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
    <Placemark>
        <name>Test Point</name>
        <Point><coordinates>-3.65,12.52,0</coordinates></Point>
    </Placemark>
</Document>
</kml>"""

@pytest.fixture
def sample_point_kml_file(sample_point_kml, tmp_path):
    f = tmp_path / "test_point.kml"
    f.write_text(sample_point_kml)
    return str(f)

@pytest.fixture
def sample_empty_kml():
    """空 KML（无 Placemark）"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><name>Empty</name></Document>
</kml>"""

@pytest.fixture
def sample_empty_kml_file(sample_empty_kml, tmp_path):
    f = tmp_path / "test_empty.kml"
    f.write_text(sample_empty_kml)
    return str(f)

@pytest.fixture
def sample_kml_with_ovcoord():
    """包含 OvCoordType 自定义元素的 KML"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
    <name>Zone 73</name>
    <Placemark>
        <name>Zone 73</name>
        <OvCoordType>CGCS2000</OvCoordType>
        <Polygon>
            <outerBoundaryIs>
                <LinearRing>
                    <coordinates>
                        -3.67481430,12.49977080,0 -3.61640520,12.49977080,0 -3.61640520,12.54162720,0 -3.67481430,12.54162720,0 -3.67481430,12.49977080,0
                    </coordinates>
                </LinearRing>
            </outerBoundaryIs>
        </Polygon>
    </Placemark>
</Document>
</kml>"""

@pytest.fixture
def sample_kml_ovcoord_file(sample_kml_with_ovcoord, tmp_path):
    f = tmp_path / "test_ovcoord.kml"
    f.write_text(sample_kml_with_ovcoord)
    return str(f)

# ═══════════════════════════════════════
# Mock Helpers
# ═══════════════════════════════════════

@pytest.fixture
def mock_requests(requests_mock):
    """预配置的 requests-mock 实例"""
    return requests_mock

@pytest.fixture
def app_client():
    """Flask test client for geo-downloader"""
    from web.app import app
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

# ═══════════════════════════════════════
# Shared test data
# ═══════════════════════════════════════

@pytest.fixture
def sample_credentials_yaml():
    """示例 credentials.yaml 内容"""
    return """
copernicus:
  username: test_user
  password: test_pass

nasa_earthdata:
  username: nasa_user
  password: nasa_pass

usgs:
  username: usgs_user
  password: usgs_pass

planet:
  api_key: PLANT1234567890abcdef

gee:
  service_account_email: test@project.iam.gserviceaccount.com
  service_account_key_path: /tmp/test_key.json

landsatxplore:
  username: ls_user
  password: ls_pass

asf:
  username: asf_user
  password: asf_pass

aws:
  access_key_id: AKIA_TEST
  secret_access_key: test_secret
"""

@pytest.fixture
def temp_credentials_file(sample_credentials_yaml, tmp_path):
    f = tmp_path / "credentials.yaml"
    f.write_text(sample_credentials_yaml)
    return str(f)
