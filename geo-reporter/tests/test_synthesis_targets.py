import json

from reporter.geocoder import LocationContext
from reporter import synthesis


def _location():
    return LocationContext(
        country="China",
        country_code="CN",
        province="",
        city="",
        district="",
        centroid_lat=42.405,
        centroid_lon=114.29,
        min_lon=114.28,
        min_lat=42.39,
        max_lon=114.30,
        max_lat=42.42,
        area_name="测试区",
        kml_description="",
    )


def test_model3d_targets_are_loaded_and_filtered_to_roi(monkeypatch, tmp_path):
    run_dir = tmp_path / "测试区" / "model3d" / "20260624_001"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(json.dumps({
        "source": "geo-model3d",
        "aoi_name": "测试区",
        "aoi_bbox": [114.28, 42.39, 114.30, 42.42],
        "crs": "EPSG:4326",
        "created_at": "2026-06-24T10:00:00",
        "products": {"targets_3d": "targets_3d.json"},
        "model_stats": {"n_targets": 2},
    }), encoding="utf-8")
    (run_dir / "targets_3d.json").write_text(json.dumps({
        "targets": [
            {"rank": 1, "lon": 114.289, "lat": 42.405, "depth_m": 1200, "score": 0.92, "uncertainty": 0.2},
            {"rank": 2, "lon": 114.240, "lat": 42.405, "depth_m": 1000, "score": 0.99, "uncertainty": 0.1},
        ]
    }), encoding="utf-8")

    monkeypatch.setattr(synthesis, "GEO_MODEL3D_OUTPUTS", str(tmp_path))

    targets = synthesis.get_model3d_targets(_location())

    assert len(targets) == 1
    assert targets[0]["source"] == "model3d"
    assert targets[0]["longitude"] == 114.289
    assert targets[0]["latitude"] == 42.405
    assert targets[0]["target_depth_m"] == 1200
    assert targets[0]["value"] == 0.92


def test_target_view_bounds_cover_roi_and_target_points():
    bounds = synthesis._target_view_bounds(_location(), [
        {"longitude": 114.295, "latitude": 42.417},
        {"longitude": 114.285, "latitude": 42.395},
    ])

    assert bounds == (114.28, 42.39, 114.30, 42.42)

    expanded = synthesis._target_view_bounds(_location(), [
        {"longitude": 114.305, "latitude": 42.425},
    ])

    assert expanded == (114.28, 42.39, 114.305, 42.425)
