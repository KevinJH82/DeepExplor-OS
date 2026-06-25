"""
P0: stats.py unit tests
Thread-safe record(), rate calculation, sort_by_rate stability.
"""
import json
import threading
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def reset_stats(monkeypatch, tmp_path):
    """Isolate stats file for each test."""
    stats_path = tmp_path / "config" / "download_stats.json"
    monkeypatch.setattr("downloader.stats._STATS_PATH", stats_path)
    yield
    # Clean up module-level cache
    import downloader.stats
    downloader.stats._lock = threading.Lock()


@pytest.mark.p0
class TestRecord:
    """Thread-safe record() function."""

    def test_record_single_success(self):
        from downloader.stats import record, load_stats
        record("sentinel2", True)
        s = load_stats()
        assert "sentinel2" in s
        assert s["sentinel2"]["attempts"] == 1
        assert s["sentinel2"]["success"] == 1
        assert s["sentinel2"]["rate"] == 1.0

    def test_record_single_failure(self):
        from downloader.stats import record, load_stats
        record("landsat", False)
        s = load_stats()
        assert s["landsat"]["attempts"] == 1
        assert s["landsat"]["success"] == 0
        assert s["landsat"]["rate"] == 0.0

    def test_record_multiple_mixed(self):
        from downloader.stats import record, load_stats
        for _ in range(3):
            record("modis", True)
        for _ in range(2):
            record("modis", False)
        s = load_stats()
        assert s["modis"]["attempts"] == 5
        assert s["modis"]["success"] == 3
        assert s["modis"]["rate"] == pytest.approx(0.6)

    def test_record_new_sensor_default_rate(self):
        from downloader.stats import record, load_stats
        record("sentinel1", True)
        s = load_stats()
        # After 1 success, rate is 1.0 not 0.5
        assert s["sentinel1"]["rate"] == 1.0

    def test_record_thread_safety(self):
        from downloader.stats import record, load_stats
        import random

        errors = []
        def worker(sensor, success):
            try:
                for _ in range(10):
                    record(sensor, success)
            except Exception as e:
                errors.append(e)

        threads = []
        sensors = ["sentinel2", "landsat", "modis", "aster", "emit"]
        for s in sensors:
            t = threading.Thread(target=worker, args=(s, True))
            threads.append(t)
            t = threading.Thread(target=worker, args=(s, False))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        s = load_stats()
        for sensor in sensors:
            assert s[sensor]["attempts"] == 20, f"{sensor}: attempts={s[sensor]['attempts']}"
            assert s[sensor]["success"] == 10, f"{sensor}: success={s[sensor]['success']}"
            assert s[sensor]["rate"] == 0.5

    def test_record_rate_precision(self):
        from downloader.stats import record, load_stats
        record("test", True)
        record("test", True)
        record("test", False)
        s = load_stats()
        # 2/3 = 0.6667 (rounded to 4 decimal places)
        assert s["test"]["rate"] == round(2 / 3, 4)
        assert s["test"]["rate"] == 0.6667


@pytest.mark.p0
class TestLoadSave:
    """Stats file I/O."""

    def test_load_empty(self):
        from downloader.stats import load_stats
        # stats_path points to a non-existent file in tmp_path
        s = load_stats()
        assert s == {}

    def test_load_corrupted_file(self, tmp_path):
        from downloader.stats import load_stats
        stats_file = tmp_path / "config" / "download_stats.json"
        stats_file.parent.mkdir()
        stats_file.write_text("not valid json{")
        # should return empty dict on parse error
        s = load_stats()
        assert s == {}

    def test_save_and_load_roundtrip(self, monkeypatch, tmp_path):
        from downloader.stats import save_stats, load_stats
        p = tmp_path / "config" / "download_stats.json"
        monkeypatch.setattr("downloader.stats._STATS_PATH", p)
        data = {"sentinel2": {"attempts": 5, "success": 4, "rate": 0.8}}
        save_stats(data)
        loaded = load_stats()
        assert loaded == data


@pytest.mark.p0
class TestSortByRate:
    """Sensor sorting by historical success rate."""

    def test_sort_descending(self, monkeypatch, tmp_path):
        from downloader.stats import save_stats, sort_by_rate
        from downloader.stats import _STATS_PATH
        p = tmp_path / "config" / "download_stats.json"
        monkeypatch.setattr("downloader.stats._STATS_PATH", p)
        save_stats({
            "sentinel2": {"attempts": 10, "success": 9, "rate": 0.9},
            "landsat": {"attempts": 10, "success": 3, "rate": 0.3},
            "modis": {"attempts": 10, "success": 7, "rate": 0.7},
        })
        sorted_list = sort_by_rate(["landsat", "modis", "sentinel2"])
        assert sorted_list == ["sentinel2", "modis", "landsat"]

    def test_sort_stable(self, monkeypatch, tmp_path):
        from downloader.stats import save_stats, sort_by_rate
        from downloader.stats import _STATS_PATH
        p = tmp_path / "config" / "download_stats.json"
        monkeypatch.setattr("downloader.stats._STATS_PATH", p)
        save_stats({
            "a": {"attempts": 10, "success": 8, "rate": 0.8},
            "b": {"attempts": 10, "success": 8, "rate": 0.8},
        })
        # Same rate: original order preserved (stable sort)
        sorted_list = sort_by_rate(["a", "b"])
        assert sorted_list == ["a", "b"]

    def test_sort_unknown_sensors_mid(self, monkeypatch, tmp_path):
        from downloader.stats import save_stats, sort_by_rate
        from downloader.stats import _STATS_PATH
        p = tmp_path / "config" / "download_stats.json"
        monkeypatch.setattr("downloader.stats._STATS_PATH", p)
        save_stats({
            "sentinel2": {"attempts": 10, "success": 9, "rate": 0.9},
            "landsat": {"attempts": 10, "success": 1, "rate": 0.1},
        })
        # Unknown gets default 0.5, should be in the middle
        sorted_list = sort_by_rate(["new_sensor", "landsat", "sentinel2"])
        assert sorted_list[0] == "sentinel2"
        assert sorted_list[-1] == "landsat"

    def test_sort_empty_stats(self):
        from downloader.stats import sort_by_rate
        sorted_list = sort_by_rate(["a", "b", "c"])
        assert sorted_list == ["a", "b", "c"]


@pytest.mark.p0
class TestGetStats:
    """get_stats() API function."""

    def test_get_stats_returns_dict(self):
        from downloader.stats import get_stats
        result = get_stats()
        assert isinstance(result, dict)

    def test_get_stats_reflects_records(self):
        from downloader.stats import record, get_stats
        record("sensor_x", True)
        result = get_stats()
        assert "sensor_x" in result
        assert result["sensor_x"]["success"] == 1
