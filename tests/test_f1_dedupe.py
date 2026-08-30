from src.f1_pipeline import addr_slug, _dedupe_key, _document_type


def test_addr_slug_reference_case():
    assert addr_slug("1500 Bay Rd #1462, Miami Beach, FL") == "1500BayRd"
    assert addr_slug("1500 Bay Rd #1462") == "1500BayRd"


def test_addr_slug_variants():
    assert addr_slug("22 elm street Apt 4B, Boston, MA") == "22ElmStreet"
    assert addr_slug("") == "Unknown"


def test_dedupe_key_matches_preexisting_unprefixed_files():
    slug = "1500BayRd"
    # The acceptance case: RoomData.csv already in the HUB must block a
    # re-upload of the prefixed name.
    assert _dedupe_key("1500BayRd_RoomData.csv", slug) == \
        _dedupe_key("RoomData.csv", slug) == "roomdata.csv"


def test_dedupe_key_case_insensitive():
    assert _dedupe_key("1500BAYRD_Mesh.GLB", "1500BayRd") == "mesh.glb"


def test_document_type_routing():
    # Text -> SharePoint ("document"); binary -> blob ("media").
    for ext in (".csv", ".txt", ".dxf", ".svg"):
        assert _document_type(ext) == "document"
    for ext in (".glb", ".ply", ".laz", ".jpg", ".png", ".pdf", ".rvt"):
        assert _document_type(ext) == "media"
