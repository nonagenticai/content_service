"""Unit tests for campaign-pack part expansion service."""

from __future__ import annotations

from src.services.scripts.campaign_pack_service import CampaignPackService


class TestCampaignPackService:
    def test_builds_variants_from_seed_part(self) -> None:
        svc = CampaignPackService()
        seed = {
            "id": "intro",
            "title": "Acme Product",
            "actions": ["Show hero"],
            "pages": ["p1"],
        }

        parts, meta = svc.build_parts(
            seed_part=seed,
            page_analysis={"p1": {"page_id": "p1", "title": "Home"}},
            script_styles=["commercial", "company"],
            variants_per_style=2,
            variant_duration_seconds=12.0,
        )

        assert len(parts) == 4
        assert len(meta) == 4
        assert all(p["estimated_duration_seconds"] == 12.0 for p in parts)
        assert all("generation_mode" in p for p in parts)
        assert {m["style"] for m in meta} == {"commercial", "company_commercial"}

    def test_infers_seed_part_when_missing(self) -> None:
        svc = CampaignPackService()
        parts, meta = svc.build_parts(
            seed_part=None,
            page_analysis={
                "home": {
                    "page_id": "home",
                    "title": "Landing",
                    "url": "https://example.com",
                }
            },
            script_styles=["webapp"],
            variants_per_style=1,
            variant_duration_seconds=15.0,
        )

        assert len(parts) == 1
        assert len(meta) == 1
        assert parts[0]["id"].startswith("campaign-webapp-commercial-v1")
        assert parts[0]["title"].startswith("Landing")
        assert parts[0]["pages"] == ["home"]
        assert parts[0]["generation_mode"] == "webapp_commercial"

    def test_rotates_custom_hooks_and_ctas(self) -> None:
        svc = CampaignPackService()
        parts, _ = svc.build_parts(
            seed_part={"id": "seed", "title": "Seed"},
            page_analysis=None,
            script_styles=["commercial"],
            variants_per_style=3,
            variant_duration_seconds=10.0,
            hooks=["H1", "H2"],
            ctas=["C1"],
        )

        actions_text = "\n".join("\n".join(p["actions"]) for p in parts)
        assert "Variant hook: H1" in actions_text
        assert "Variant hook: H2" in actions_text
        assert actions_text.count("Ending CTA: C1") == 3

    def test_coerces_title_card_seed_to_browser_recording(self) -> None:
        svc = CampaignPackService()
        seed = {"id": "intro", "title": "Intro", "part_type": "title_card"}

        parts, _ = svc.build_parts(
            seed_part=seed,
            page_analysis=None,
            script_styles=["commercial"],
            variants_per_style=1,
            variant_duration_seconds=10.0,
        )

        assert parts[0]["part_type"] == "browser_recording"
        assert seed["part_type"] == "title_card"
