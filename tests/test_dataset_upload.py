"""Loading a whole new dataset over HTTP: files matched by name or columns, a zip, errors, and back to the sample."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import api.main as main
from engine.notify import Notifier
from engine.service import RelayService
from engine.storage import LocalStorage
from tests.test_robustness import transformed_kit  # noqa: F401  (pytest fixture)


@pytest.fixture()
def client(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("RELAY_TICKER", "0")
    main.set_service(RelayService(cfg, LocalStorage(tmp_path / "state"), Notifier()))
    with TestClient(main.app) as c:
        yield c


def _zip(folder, rename=lambda n: n):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(folder.glob("*.csv")):
            z.writestr(f"new data/{rename(p.name)}", p.read_text())
    return buf.getvalue()


def test_new_dataset_from_zip_with_other_names_ids_and_times(client, transformed_kit):  # noqa: F811
    kit, mapping = transformed_kit
    # File names a judge might use, plus meaningless ones that only the columns identify.
    names = {"hospitals.csv": "Hospital list.csv", "physicians_on_shift.csv": "doctors.csv",
             "credentialing_matrix.csv": "matrix.csv", "event_stream.csv": "Events (export).csv"}
    r = client.post("/api/dataset", files={"files": ("kit.zip", _zip(kit, lambda n: names.get(n, n)), "application/zip")}).json()
    assert r["ok"], r
    assert r["dataset"]["source"] == "uploaded"
    assert r["dataset"]["hospitals"] == 12 and r["dataset"]["physicians"] == 14
    assert r["issues"] > 0
    s = client.get("/api/state").json()
    assert {h["id"] for h in s["hospitals"]} == {mapping[f"SYN-{i:03d}"] for i in range(1, 13)}
    assert s["now"].startswith(s["dataset"]["scenario_date"])
    assert s["data_health"]["unknown_physicians"] == [] and s["data_health"]["unknown_hospitals"] == []
    # Reset keeps the uploaded dataset; going back to the sample restores 6:20 PM.
    client.post("/api/reset")
    assert client.get("/api/state").json()["dataset"]["source"] == "uploaded"
    back = client.post("/api/dataset/sample").json()
    assert back["dataset"]["source"] == "sample"
    assert client.get("/api/state").json()["now_label"] == "6:20 PM ET"


def test_missing_required_file_is_rejected_and_nothing_changes(client):
    kit = main.REPO_ROOT / "data" / "kit"
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(kit.glob("*.csv")) if p.name != "hospitals.csv"]
    r = client.post("/api/dataset", files=files).json()
    assert not r["ok"]
    assert any("Hospitals" in e for e in r["errors"])
    assert client.get("/api/state").json()["dataset"]["source"] == "sample"


def test_individual_csv_files_load(client):
    kit = main.REPO_ROOT / "data" / "kit"
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(kit.glob("*.csv"))]
    r = client.post("/api/dataset", files=files).json()
    assert r["ok"], r
    snapshots = [f for f in r["files"] if "snapshot" in f["name"]]
    assert snapshots and all(f["status"] == "ignored" for f in snapshots)
