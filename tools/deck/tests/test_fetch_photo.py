from pathlib import Path

import pytest

import fetch_photo as fp


@pytest.mark.parametrize(
    "name,ok",
    [
        ("CC0", True), ("CC BY 4.0", True), ("CC BY-SA 4.0", True), ("CC BY-SA 2.0 KR", True),
        ("Public domain", True), ("PD-self", True), ("CC BY-NC 4.0", False), ("CC BY-ND 2.0", False),
        ("CC BY-NC-SA 3.0", False), ("GFDL", False), ("", False), ("Fair use", False),
    ],
)
def test_license_allowed(name: str, ok: bool) -> None:
    assert fp.license_allowed(name) is ok


def test_strip_html() -> None:
    raw = '<a href="x">Mobius6</a> &amp; <b>co</b>\n  friends'
    assert fp.strip_html(raw) == "Mobius6 & co friends"


def _page(mime: str = "image/jpeg", desc: str = "A fire station", cats: str = "Fire stations", lic: str = "CC BY-SA 4.0") -> dict:
    return {
        "title": "File:X.jpg",
        "imageinfo": [{
            "mime": mime, "width": 3000, "height": 2000, "thumburl": "https://t/x.jpg",
            "descriptionurl": "https://p/x",
            "extmetadata": {
                "Artist": {"value": '<a href="u">Ann</a>'}, "LicenseShortName": {"value": lic},
                "LicenseUrl": {"value": "https://cc"}, "ImageDescription": {"value": desc},
                "Categories": {"value": cats},
            },
        }],
    }


def test_parse_page_basic() -> None:
    p = fp.parse_page(_page())
    assert p is not None and p.author == "Ann" and not p.ai_flagged and p.mime == "image/jpeg"


@pytest.mark.parametrize("field", ["desc", "cats"])
def test_ai_flag(field: str) -> None:
    kwargs = {field: "AI-generated images|Midjourney"}
    p = fp.parse_page(_page(**kwargs))
    assert p is not None and p.ai_flagged


def test_parse_page_without_imageinfo() -> None:
    assert fp.parse_page({"title": "File:Y.jpg"}) is None


def test_search_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {**_page(), "title": "File:ok.jpg", "index": 3},
        {**_page(mime="image/png"), "title": "File:d.png", "index": 1},
        {**_page(lic="CC BY-NC 4.0"), "title": "File:nc.jpg", "index": 2},
        {**_page(desc="ai-generated"), "title": "File:ai.jpg", "index": 4},
    ]
    monkeypatch.setattr(fp, "_api_get", lambda params: {"query": {"pages": pages}})
    assert [p.title for p in fp.search_photos("q", 8)] == ["File:ok.jpg"]


def test_get_refuses_bad_license(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fp, "_api_get", lambda params: {"query": {"pages": [_page(lic="CC BY-NC 4.0")]}})
    with pytest.raises(fp.LicenseRefused):
        fp.get_photo("File:X.jpg")


def test_get_refuses_svg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fp, "_api_get", lambda params: {"query": {"pages": [_page(mime="image/svg+xml")]}})
    with pytest.raises(fp.FetchError):
        fp.get_photo("File:X.svg")


def test_credits_append_and_line(tmp_path: Path) -> None:
    p = fp.parse_page(_page())
    assert p is not None
    fp.append_credit(tmp_path, "a.jpg", p)
    fp.append_credit(tmp_path, "b.jpg", p)
    rows = (tmp_path / "CREDITS.md").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("# Photo credits") and sum(r.startswith("| a.jpg") or r.startswith("| b.jpg") for r in rows) == 2
    assert fp.credit_line_ko("Ann", "CC BY-SA 4.0") == "사진: Ann, CC BY-SA 4.0, Wikimedia Commons"


def test_safe_filename() -> None:
    assert fp.safe_filename("File:A b/c, d.jpg") == "A_b_c_d.jpg"


def test_search_limit_validation() -> None:
    with pytest.raises(ValueError):
        fp.search_photos("q", 0)
