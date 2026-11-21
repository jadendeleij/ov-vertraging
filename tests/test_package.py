"""Het Release-pakket van de serving-export: vaste bestandenlijst, label en checksum."""

import hashlib
import json
import tarfile

from ov_vertraging.serving.export import package

TABLES = [
    "timetable", "hist_route_hour", "hist_station", "hist_train", "planned", "network",
    "stations", "school_holidays", "insights_station", "insights_hour", "insights_weather",
]  # fmt: skip


def _serving_dir(tmp_path):
    d = tmp_path / "serving"
    d.mkdir()
    for t in TABLES:
        (d / f"{t}.parquet").write_bytes(b"x")
    (d / "model.txt").write_text("model")
    (d / "model_metadata.json").write_text("{}")
    (d / "manifest.json").write_text(json.dumps({"model_version": "20261121"}))
    (d / ".env").write_text("GEHEIM=1")  # mag nooit in het pakket
    return d


def test_pakket_met_label_en_checksum(tmp_path):
    archive = package(_serving_dir(tmp_path), tmp_path, label="20261121-web")
    assert archive.name == "serving-20261121-web.tar.gz"
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert ".env" not in names
    assert {"insights_station.parquet", "model.txt", "manifest.json"} <= names
    digest, name = (tmp_path / "serving-20261121-web.tar.gz.sha256").read_text().split()
    assert name == archive.name
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_pakket_zonder_label_heet_naar_modelversie(tmp_path):
    assert package(_serving_dir(tmp_path), tmp_path).name == "serving-20261121.tar.gz"
