import gzip
import importlib.util
import json
import lzma
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bus_network_snapshot.py"


def load():
    spec = importlib.util.spec_from_file_location("bus_network_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_polls_are_xz_and_processed_files_stay_gzip():
    snapshot = load()
    rows = [{"vehicleId": f"LX{i:03d}", "lineId": "29", "timeToStation": i * 30} for i in range(500)]
    xz, gz = snapshot.ndjson(rows, xz=True), snapshot.ndjson(rows)
    assert [json.loads(line) for line in lzma.decompress(xz).splitlines()] == rows
    assert [json.loads(line) for line in gzip.decompress(gz).splitlines()] == rows   # BigQuery reads gzip
    assert len(xz) < len(gz)
