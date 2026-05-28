"""Asset discriminator compatibility for SDK-generated creative schemas."""

from src.core.schemas import CreativeAsset
from src.core.schemas._asset_type_compat import infer_asset_types, normalize_assets_for_wire


def test_infer_asset_types_demotes_url_only_image_key_to_url() -> None:
    assets = {"image": {"url": "https://example.com/banner.png"}}

    assert infer_asset_types(assets) == {"image": {"url": "https://example.com/banner.png", "asset_type": "url"}}


def test_infer_asset_types_keeps_dimensioned_image_as_image() -> None:
    assets = {"image": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}

    assert infer_asset_types(assets) == {
        "image": {
            "url": "https://example.com/banner.png",
            "width": 300,
            "height": 250,
            "asset_type": "image",
        }
    }


def test_local_creative_asset_uses_shared_asset_type_compat() -> None:
    creative = CreativeAsset(
        creative_id="creative-1",
        name="Creative",
        format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_image"},
        assets={"image": {"url": "https://example.com/banner.png"}},
    )

    assert normalize_assets_for_wire(creative.assets) == {
        "image": {"asset_type": "url", "url": "https://example.com/banner.png"}
    }
