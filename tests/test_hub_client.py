import pytest

from src.hub_client import decode_hub_error, redact_url, HubError


def test_decoder_two_id_trap():
    decoded = decode_hub_error("Not found. Check that the id is correct.",
                               used_url_mode=False)
    assert "dealId" in decoded and "project" in decoded.lower()


def test_decoder_unauthorized_on_url_mode_blames_remote():
    decoded = decode_hub_error(
        "Unauthorized. The HUB_API_KEY is missing or invalid.",
        used_url_mode=True)
    assert "REMOTE" in decoded
    assert "API key is fine" in decoded


def test_decoder_400_means_allowlist():
    decoded = decode_hub_error("Unexpected error — HUB upload failed (400)",
                               used_url_mode=False)
    assert "allowlist" in decoded
    assert "zip" in decoded.lower()


def test_unknown_message_flagged_suspicious():
    assert "suspicion" in decode_hub_error("weird new error", used_url_mode=False)


def test_huberror_carries_both_messages():
    e = HubError("Not found. Check that the id is correct.",
                 decode_hub_error("Not found. Check that the id is correct.",
                                  used_url_mode=False), status=404)
    assert "actually means" in str(e)
    assert e.status == 404


def test_redact_url_strips_query():
    assert redact_url("https://cdn.poly.cam/a/b.glb?sig=SECRET") == \
        "https://cdn.poly.cam/a/b.glb?<redacted>"
    assert redact_url("https://cdn.poly.cam/a/b.glb") == \
        "https://cdn.poly.cam/a/b.glb"


def test_base64_cap_enforced(tmp_path, monkeypatch):
    from src import hub_client
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * (hub_client.BASE64_MAX + 1))
    monkeypatch.setenv("HUB_BASE_URL", "https://hub.example")
    monkeypatch.setenv("HUB_API_KEY", "k")
    client = hub_client.HubClient()
    with pytest.raises(ValueError, match="contentBase64 caps"):
        client.upload_via_base64("deal", "Plans and 3D", "big.bin", big)


def test_placeholder_endpoint_refuses(monkeypatch):
    from src import hub_client
    monkeypatch.setenv("HUB_BASE_URL", "https://hub.example")
    monkeypatch.setenv("HUB_API_KEY", "k")
    client = hub_client.HubClient()
    with pytest.raises(RuntimeError, match="placeholder"):
        client.list_sales_deals()
