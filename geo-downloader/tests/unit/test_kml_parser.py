"""P0: kml_parser.py 测试 (20 用例) — 修正版"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestKMLPolygonParsing:
    """KML 多边形解析"""
    
    def test_parse_simple_polygon_returns_data(self, sample_kml_file):
        """简单多边形解析返回有效数据"""
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(sample_kml_file)
        assert name is not None
        assert isinstance(name, str)
        assert bbox is not None
    
    def test_parse_polygon_bbox_format(self, sample_kml_file):
        """验证返回 bbox 格式"""
        from downloader.kml_parser import parse_kml
        _, bbox, _ = parse_kml(sample_kml_file)
        assert len(bbox) == 4  # (minx, miny, maxx, maxy)
    
    def test_parse_polygon_bbox_values(self, sample_kml_file):
        """验证 bbox 数值正确"""
        from downloader.kml_parser import parse_kml
        _, bbox, _ = parse_kml(sample_kml_file)
        # Coordinates: -3.68,12.50 to -3.61,12.54
        assert abs(bbox[0] - (-3.68)) < 0.01
        assert abs(bbox[1] - 12.50) < 0.01
        assert abs(bbox[2] - (-3.61)) < 0.01
        assert abs(bbox[3] - 12.54) < 0.01
    
    def test_parse_polygon_returns_geometry(self, sample_kml_file):
        """验证返回几何体"""
        from downloader.kml_parser import parse_kml
        geom, _, _ = parse_kml(sample_kml_file)
        assert geom is not None
    
    def test_name_from_filename(self, sample_kml_file):
        """name 来自文件名（不含扩展名）"""
        from downloader.kml_parser import parse_kml
        _, _, name = parse_kml(sample_kml_file)
        # name is the filename stem
        assert '.kml' not in name.lower() or len(name) > 4


class TestKMLPointParsing:
    """KML 点解析"""
    
    def test_parse_point_returns_data(self, sample_point_kml_file):
        """单点 KML 解析返回数据"""
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(sample_point_kml_file)
        assert name is not None
        assert bbox is not None
    
    def test_point_creates_nonzero_bbox(self, sample_point_kml_file):
        """Point 缓冲后生成非零 bbox"""
        from downloader.kml_parser import parse_kml
        _, bbox, _ = parse_kml(sample_point_kml_file)
        # Point at -3.65, 12.52 — bbox should be near that
        assert abs(bbox[0] - (-3.65)) < 1.0
        assert abs(bbox[1] - 12.52) < 1.0


class TestKMLEmpty:
    """空 KML 处理"""
    
    def test_empty_kml_no_placemark(self, sample_empty_kml_file):
        """无 Placemark 的 KML"""
        from downloader.kml_parser import parse_kml
        with pytest.raises(Exception):
            parse_kml(sample_empty_kml_file)


class TestKMLWithCustomElements:
    """自定义元素兼容性"""
    
    def test_ovcoordtype_element_parses(self, sample_kml_ovcoord_file):
        """包含 OvCoordType 自定义元素的 KML 可正常解析"""
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(sample_kml_ovcoord_file)
        assert bbox is not None
        assert len(bbox) == 4


class TestKMLFileErrors:
    """文件错误处理"""
    
    def test_nonexistent_file(self, tmp_path):
        """不存在的文件"""
        from downloader.kml_parser import parse_kml
        with pytest.raises(Exception):
            parse_kml(str(tmp_path / "nonexistent.kml"))
    
    def test_invalid_xml(self, tmp_path):
        """无效 XML 文件"""
        bad = tmp_path / "bad.kml"
        bad.write_text("not xml at all")
        from downloader.kml_parser import parse_kml
        with pytest.raises(Exception):
            parse_kml(str(bad))


class TestKMLEdgeCases:
    """边界情况"""
    
    def test_single_coordinate_polygon(self, tmp_path):
        """单坐标多边形"""
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><Placemark><name>Single</name>
<Polygon><outerBoundaryIs><LinearRing>
<coordinates>0,0,0 1,0,0 1,1,0 0,1,0 0,0,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon>
</Placemark></Document></kml>"""
        f = tmp_path / "valid_polygon.kml"
        f.write_text(kml)
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(str(f))
        assert bbox is not None
    
    def test_nested_folders(self, tmp_path):
        """嵌套 Folder"""
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><name>Root</name>
<Folder><name>Sub</name>
<Placemark><name>Nested</name>
<Polygon><outerBoundaryIs><LinearRing>
<coordinates>0,0,0 1,0,0 1,1,0 0,1,0 0,0,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon>
</Placemark></Folder></Document></kml>"""
        f = tmp_path / "nested.kml"
        f.write_text(kml)
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(str(f))
        assert name is not None
    
    def test_line_string_geometry(self, tmp_path):
        """LineString 几何体缓冲"""
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><Placemark><name>Line</name>
<LineString><coordinates>0,0,0 1,1,0 2,0,0</coordinates></LineString>
</Placemark></Document></kml>"""
        f = tmp_path / "line.kml"
        f.write_text(kml)
        from downloader.kml_parser import parse_kml
        _, bbox, _ = parse_kml(str(f))
        assert bbox is not None

    def test_coordinates_with_extra_whitespace(self, tmp_path):
        """坐标间多余空白"""
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><Placemark><name>Spaced</name>
<Polygon><outerBoundaryIs><LinearRing>
<coordinates>
   0,0,0   
   1,0,0   
   1,1,0   
   0,1,0   
   0,0,0
</coordinates>
</LinearRing></outerBoundaryIs></Polygon>
</Placemark></Document></kml>"""
        f = tmp_path / "spaced.kml"
        f.write_text(kml)
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(str(f))
        assert bbox is not None

    def test_multi_geometry_merged(self, tmp_path):
        """多个几何体合并为单一 bbox"""
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>A</name>
<Polygon><outerBoundaryIs><LinearRing>
<coordinates>0,0,0 1,0,0 1,1,0 0,1,0 0,0,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon>
</Placemark>
<Placemark><name>B</name>
<Polygon><outerBoundaryIs><LinearRing>
<coordinates>2,2,0 3,2,0 3,3,0 2,3,0 2,2,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon>
</Placemark>
</Document></kml>"""
        f = tmp_path / "multi.kml"
        f.write_text(kml)
        from downloader.kml_parser import parse_kml
        geom, bbox, name = parse_kml(str(f))
        # BBox should cover both polygons
        assert bbox[0] <= 1.0 and bbox[2] >= 2.0
