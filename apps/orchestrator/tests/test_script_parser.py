from __future__ import annotations

from muse_orchestrator.services.script_parser import ScriptAnalyzerService


def test_slugify_transliterates_vietnamese_diacritics() -> None:
    proposal = ScriptAnalyzerService.analyze_script(
        "SCENE 1: Quán Cà Phê Poirot\n"
        "SHOT 1: Nhân vật bước vào\n"
        "- Character: Đặng Thị Hà\n"
        "- Location: Phòng Khách\n"
    )

    assert proposal.scenes[0]["location_code"] == "loc_quan_ca_phe_poirot"
    assert proposal.characters[0]["code_name"] == "char_dang_thi_ha"
    assert any(location["code_name"] == "loc_phong_khach" for location in proposal.locations)


def test_slugify_keeps_ascii_identifiers_unchanged() -> None:
    proposal = ScriptAnalyzerService.analyze_script(
        "SCENE 1: Detective Office\n"
        "SHOT 1: Conan investigates\n"
        "- Character: Conan Edogawa\n"
    )

    assert proposal.scenes[0]["location_code"] == "loc_detective_office"
    assert proposal.characters[0]["code_name"] == "char_conan_edogawa"
