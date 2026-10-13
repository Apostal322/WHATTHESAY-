"""Deterministic ranking, deduplication and passage extraction."""

from app.services.evidence import (
    MAX_PASSAGE_CHARS,
    ClaimTerms,
    canonical_url,
    excerpt_is_verbatim,
    extract_passages,
    lexical_relevance,
    rank_hits,
    site_of,
)
from app.services.search import SearchHit

CLAIM = "The Eiffel Tower was completed in 1889 for the World's Fair."
TERMS = ClaimTerms.build(CLAIM, ["Eiffel Tower completion year"])


def test_canonical_url_merges_tracking_www_amp_and_fragments() -> None:
    a = canonical_url("https://www.example.org/story/amp/?utm_source=x&id=7#top")
    b = canonical_url("http://example.org/story?id=7&fbclid=abc")
    c = canonical_url("https://m.example.org/story/")
    assert a == b == canonical_url("https://example.org/story?id=7")
    assert c == canonical_url("https://example.org/story")


def test_site_of_groups_subdomains_and_multi_part_suffixes() -> None:
    assert site_of("https://edition.cnn.com/x") == "cnn.com"
    assert site_of("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert site_of("https://ria.ru/x") == "ria.ru"


def test_lexical_relevance_uses_prefix_stems_for_inflected_languages() -> None:
    terms = ClaimTerms.build("Пелевин написал роман о буддизме", [])
    assert lexical_relevance("В романе Пелевина много буддизма", terms) > 0.5
    assert lexical_relevance("Погода в Москве", terms) == 0.0


def test_lexical_relevance_numbers_bonus() -> None:
    no_num = lexical_relevance("The Eiffel Tower was completed for the fair", TERMS)
    with_num = lexical_relevance("The Eiffel Tower was completed in 1889 for the fair", TERMS)
    assert with_num > no_num


def test_rank_hits_fuses_queries_dedupes_filters_and_caps_per_site() -> None:
    hits = {
        "q1": [
            SearchHit(
                "https://www.toureiffel.paris/en/history",
                "Eiffel Tower history",
                "Completed in 1889 for the World's Fair",
                1,
            ),
            SearchHit(
                "https://en.wikipedia.org/wiki/Eiffel_Tower",
                "Eiffel Tower",
                "Wrought-iron tower completed 1889",
                2,
            ),
            SearchHit("https://example.com/recipes", "Pancakes", "Best pancake recipe", 3),
            SearchHit("http://127.0.0.1/admin", "Eiffel Tower 1889", "Eiffel Tower completed", 4),
        ],
        "q2": [
            SearchHit(
                "https://en.wikipedia.org/wiki/Eiffel_Tower?utm_source=x",
                "Eiffel Tower",
                "Tower completed in 1889",
                1,
            ),
            SearchHit(
                "https://fr.wikipedia.org/wiki/Tour_Eiffel",
                "Tour Eiffel",
                "Eiffel Tower completed 1889 World's Fair",
                2,
            ),
            SearchHit(
                "https://de.wikipedia.org/wiki/Eiffelturm",
                "Eiffelturm",
                "Eiffel Tower completed 1889",
                3,
            ),
        ],
    }
    ranked = rank_hits(hits, TERMS, limit=10, per_domain=2)
    urls = [r.url for r in ranked]
    assert urls[0] == "https://en.wikipedia.org/wiki/Eiffel_Tower"  # found by both queries
    assert ranked[0].found_by == {"q1", "q2"}
    assert "https://example.com/recipes" not in urls  # irrelevant
    assert not any("127.0.0.1" in u for u in urls)  # unsafe
    assert sum(1 for r in ranked if r.site == "wikipedia.org") == 2  # per-site cap
    assert "https://www.toureiffel.paris/en/history" in urls


def test_extract_passages_are_verbatim_bounded_and_relevant() -> None:
    long_para = (
        "Gustave Eiffel's company designed and built the tower. "
        "The Eiffel Tower was completed in March 1889 as the entrance to the 1889 World's Fair. "
        + "It was criticised by some artists at the time. "
        * 15
    )
    paragraphs = [
        "Home | Tickets | Shop",
        long_para,
        "Opening hours vary by season and weather conditions.",
    ]
    passages = extract_passages(paragraphs, TERMS)
    assert passages
    assert all(len(p) <= MAX_PASSAGE_CHARS for p in passages)
    assert all(excerpt_is_verbatim(p, paragraphs) for p in passages)
    assert any("completed in March 1889" in p for p in passages)
    assert not any("Opening hours" in p for p in passages)


def test_excerpt_is_verbatim_rejects_paraphrase() -> None:
    assert not excerpt_is_verbatim(
        "The tower was finished in 1889.", ["The tower was completed in 1889."]
    )


def test_cross_language_evidence_matches_its_own_query() -> None:
    terms = ClaimTerms.build(
        "Виктор Пелевин — автор романа «Чапаев и Пустота».",
        ["Пелевин Чапаев и Пустота роман", "Chapaev and Void Pelevin novel"],
    )
    english_page = "Chapaev and Void is a 1996 novel by the Russian writer Victor Pelevin."
    assert lexical_relevance(english_page, terms) >= 0.5
    assert lexical_relevance("A recipe for borscht with beetroot.", terms) == 0.0
