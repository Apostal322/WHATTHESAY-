import pytest

from app.core.errors import InvalidInputError
from app.services.youtube import canonical_watch_url, format_mmss, parse_video_id

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=755s",
        f"https://m.youtube.com/watch?feature=share&v={VID}",
        f"https://youtu.be/{VID}?t=10",
        f"http://www.youtube.com/watch?v={VID}",
    ],
)
def test_parse_valid_urls(url: str) -> None:
    assert parse_video_id(youtube_url=url) == VID


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        (f"https://evil.example/watch?v={VID}", "allowed YouTube host"),
        (f"https://www.youtube.com.evil.example/watch?v={VID}", "allowed YouTube host"),
        (f"https://user:pw@www.youtube.com/watch?v={VID}", "plain http"),
        (f"https://www.youtube.com:8443/watch?v={VID}", "plain http"),
        (f"file:///etc/passwd?v={VID}", "plain http"),
        (f"https://www.youtube.com/shorts/{VID}", "Shorts"),
        (f"https://www.youtube.com/live/{VID}", "Live"),
        ("https://www.youtube.com/watch?v=short", "valid YouTube video ID"),
        ("https://www.youtube.com/embed/xyz", "watch URLs"),
    ],
)
def test_parse_rejects_unsafe_or_unsupported(url: str, fragment: str) -> None:
    with pytest.raises(InvalidInputError, match=fragment):
        parse_video_id(youtube_url=url)


def test_video_id_must_match_url() -> None:
    with pytest.raises(InvalidInputError, match="does not match"):
        parse_video_id(youtube_url=f"https://youtu.be/{VID}", video_id="aaaaaaaaaaa")


def test_explicit_id_validated() -> None:
    assert parse_video_id(video_id=VID) == VID
    with pytest.raises(InvalidInputError):
        parse_video_id(video_id="../../etc/x")


def test_canonical_url_is_rebuilt() -> None:
    assert canonical_watch_url(VID) == f"https://www.youtube.com/watch?v={VID}"


def test_format_mmss() -> None:
    assert format_mmss(755) == "12:35"
    assert format_mmss(3725) == "1:02:05"
    assert format_mmss(-3) == "00:00"
