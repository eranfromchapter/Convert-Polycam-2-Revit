from src.manifest import Manifest


def test_roundtrip_and_idempotency(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest(path)
    m.deal("d1", address="1500 Bay Rd #1462", capture_id="cap1")
    m.record("d1", "a.glb", "verified", size=100, folder="Plans and 3D")
    m.record("d1", "b.ply", "uploaded", size=200)
    m.record("d1", "c.csv", "skipped_existing")

    m2 = Manifest(path)  # fresh process
    assert m2.is_done("d1", "a.glb")
    assert m2.is_done("d1", "c.csv")
    # 'uploaded' is NOT done until verification confirms it
    assert not m2.is_done("d1", "b.ply")
    assert m2.deal("d1")["address"] == "1500 Bay Rd #1462"


def test_source_urls_are_redacted(tmp_path):
    m = Manifest(tmp_path / "m.json")
    m.record("d1", "a.glb", "uploaded",
             source_url="https://cdn.poly.cam/x/a.glb?sig=SECRET&exp=123")
    stored = m.deal("d1")["files"]["a.glb"]["source_url"]
    assert "SECRET" not in stored and "sig=" not in stored
    assert (tmp_path / "m.json").read_text().count("SECRET") == 0


def test_f2_queue_selection(tmp_path):
    m = Manifest(tmp_path / "m.json")
    m.deal("d1")
    m.deal("d2")
    m.mark_f1_complete("d1")
    m.mark_f1_complete("d2")
    m.mark_f2_complete("d2")
    assert m.f1_complete_pending_f2() == ["d1"]


def test_report_totals(tmp_path):
    m = Manifest(tmp_path / "m.json")
    m.deal("d1", address="A")
    m.record("d1", "a.glb", "verified", size=100)
    m.record("d1", "b.ply", "failed", error="boom")
    m.record("d1", "c.csv", "skipped_existing")
    m.record("d1", "d.laz", "flagged_oversize", size=99_000_000)
    r = m.report()
    assert r["totals"] == {"files": 4, "verified": 1, "skipped": 1,
                           "failed": 1, "flagged": 1, "bytes": 100}


def test_error_cleared_on_success(tmp_path):
    m = Manifest(tmp_path / "m.json")
    m.record("d1", "a.glb", "failed", error="transient")
    m.record("d1", "a.glb", "verified")
    assert "error" not in m.deal("d1")["files"]["a.glb"]
