"""测试锚点分析模块"""

import pytest

from dj_set_curator.anchor import AnchorAnalyzer, AnchorSong
from dj_set_curator.models import Song


class TestIsSongId:
    def test_pure_number(self):
        assert AnchorAnalyzer._is_song_id("29732235") is True

    def test_with_spaces(self):
        assert AnchorAnalyzer._is_song_id("  29732235  ") is True

    def test_artist_song_format(self):
        assert AnchorAnalyzer._is_song_id("Aphex Twin - Windowlicker") is False

    def test_empty(self):
        assert AnchorAnalyzer._is_song_id("") is False


class TestParseArtistSong:
    def test_standard_format(self):
        artist, song = AnchorAnalyzer._parse_artist_song("Aphex Twin - Windowlicker")
        assert artist == "Aphex Twin"
        assert song == "Windowlicker"

    def test_en_dash(self):
        artist, song = AnchorAnalyzer._parse_artist_song("Boards of Canada – Roygbiv")
        assert artist == "Boards of Canada"
        assert song == "Roygbiv"

    def test_no_separator(self):
        artist, song = AnchorAnalyzer._parse_artist_song("Windowlicker")
        assert artist == ""
        assert song == "Windowlicker"

    def test_extra_spaces(self):
        artist, song = AnchorAnalyzer._parse_artist_song("  Artist  -  Song  ")
        assert artist == "Artist"
        assert song == "Song"


class TestAnchorSongDataclass:
    def test_creation(self):
        a = AnchorSong(id="123", name="Test", artist="Artist")
        assert a.id == "123"
        assert a.name == "Test"
        assert a.artist == "Artist"
        assert a.bpm is None

    def test_with_optional(self):
        a = AnchorSong(id="123", name="Test", artist="Artist", bpm=128.0, key="8B")
        assert a.bpm == 128.0
        assert a.key == "8B"


@pytest.mark.asyncio
class TestResolveAnchor:
    async def test_resolve_by_id(self, monkeypatch):
        analyzer = AnchorAnalyzer()

        class FakeMCP:
            async def get_song_detail(self, song_id):
                return {"id": 29732235, "name": "Windowlicker", "artist": "Aphex Twin"}

            async def search_song(self, keyword):
                return [Song(id="29732235", name="Windowlicker", artist="Aphex Twin")]

        mcp = FakeMCP()
        result = await analyzer.resolve_anchor("29732235", mcp)
        assert result.id == "29732235"
        assert result.name == "Windowlicker"
        assert result.artist == "Aphex Twin"

    async def test_resolve_by_name(self, monkeypatch):
        analyzer = AnchorAnalyzer()

        class FakeMCP:
            async def search_song(self, keyword):
                return [
                    Song(id="1", name="Windowlicker", artist="Aphex Twin"),
                ]

        mcp = FakeMCP()
        result = await analyzer.resolve_anchor("Windowlicker", mcp)
        assert result.name == "Windowlicker"

    async def test_resolve_artist_song_format(self, monkeypatch):
        analyzer = AnchorAnalyzer()

        class FakeMCP:
            async def search_song(self, keyword):
                return [
                    Song(id="1", name="Roygbiv", artist="Boards of Canada"),
                    Song(id="2", name="Roygbiv Cover", artist="Someone Else"),
                ]

        mcp = FakeMCP()
        result = await analyzer.resolve_anchor("Boards of Canada - Roygbiv", mcp)
        assert result.artist == "Boards of Canada"
        assert result.name == "Roygbiv"

    async def test_empty_query(self):
        analyzer = AnchorAnalyzer()

        class FakeMCP:
            pass

        with pytest.raises(ValueError, match="不能为空"):
            await analyzer.resolve_anchor("", FakeMCP())

    async def test_no_results(self):
        analyzer = AnchorAnalyzer()

        class FakeMCP:
            async def search_song(self, keyword):
                return []

        with pytest.raises(ValueError, match="未找到结果"):
            await analyzer.resolve_anchor("Nonexistent Song 12345", FakeMCP())


@pytest.mark.asyncio
class TestResolveMultiple:
    async def test_multiple_anchors(self):
        analyzer = AnchorAnalyzer()
        calls = []

        class FakeMCP:
            async def get_song_detail(self, song_id):
                return {"id": song_id, "name": f"Song {song_id}", "artist": "Artist"}

            async def search_song(self, keyword):
                calls.append(keyword)
                return [Song(id=keyword, name=f"Song {keyword}", artist="Artist")]

        mcp = FakeMCP()
        results = await analyzer.resolve_multiple(["111", "222"], mcp)
        assert len(results) == 2
        assert results[0].id == "111"
        assert results[1].id == "222"
