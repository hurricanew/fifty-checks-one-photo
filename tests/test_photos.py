from app.config import ROOT


def test_committed_photos_are_jpegs_and_attributed():
    folder = ROOT / "photos"
    names = sorted(path.name for path in folder.glob("*.jpg"))
    assert 3 <= len(names) <= 5
    attribution = (folder / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "CC BY 2.0" in attribution
    assert "CC BY 4.0" in attribution
    assert "CC BY-SA 4.0" in attribution
    assert "rack" not in attribution.lower()
    for name in names:
        assert name in attribution
        data = (folder / name).read_bytes()
        assert data[:3] == b"\xff\xd8\xff"
