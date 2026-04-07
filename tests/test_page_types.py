from __future__ import annotations

from compile.page_types import (
    ARTICLE_PAGE_TYPES,
    CONTENT_PAGE_TYPES,
    DEFAULT_PAGE_TYPES,
    MAP_PAGE_TYPES,
    MATURITY_STATES,
    NAV_PAGE_TYPES,
    OUTPUT_PAGE_TYPES,
)


class TestPageTypeSets:
    def test_article_types_are_content(self) -> None:
        assert ARTICLE_PAGE_TYPES.issubset(CONTENT_PAGE_TYPES)

    def test_output_types_are_content(self) -> None:
        assert OUTPUT_PAGE_TYPES.issubset(CONTENT_PAGE_TYPES)

    def test_nav_types_disjoint_from_content(self) -> None:
        assert NAV_PAGE_TYPES.isdisjoint(CONTENT_PAGE_TYPES)

    def test_default_types_include_core(self) -> None:
        for expected in ("source", "article", "map", "output", "index", "overview", "log"):
            assert expected in DEFAULT_PAGE_TYPES

    def test_maturity_states(self) -> None:
        assert MATURITY_STATES == {"seed", "emerging", "stable"}

    def test_article_includes_legacy_types(self) -> None:
        for legacy in ("concept", "entity", "question"):
            assert legacy in ARTICLE_PAGE_TYPES

    def test_map_includes_dashboard(self) -> None:
        assert "dashboard" in MAP_PAGE_TYPES
