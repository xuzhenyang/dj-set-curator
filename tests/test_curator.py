"""测试选曲引擎核心"""

import pytest

from dj_set_curator.anchor import AnchorSong
from dj_set_curator.curator import DJSetCurator
from dj_set_curator.filters import ScoredSong


class TestDeduplicate:
    def test_removes_duplicates(self):
        songs = [
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
            {"id": "1", "name": "A Duplicate"},
        ]
        result = DJSetCurator._deduplicate(songs)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "2"

    def test_empty_list(self):
        assert DJSetCurator._deduplicate([]) == []

    def test_no_duplicates(self):
        songs = [
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
        ]
        assert DJSetCurator._deduplicate(songs) == songs


class TestRemoveAnchors:
    def test_removes_anchor_songs(self):
        candidates = [
            {"id": "1", "name": "Anchor Song"},
            {"id": "2", "name": "Candidate"},
        ]
        anchors = [AnchorSong(id="1", name="Anchor Song", artist="Artist")]
        result = DJSetCurator._remove_anchors(candidates, anchors)
        assert len(result) == 1
        assert result[0]["id"] == "2"

    def test_no_overlap(self):
        candidates = [
            {"id": "2", "name": "Candidate"},
        ]
        anchors = [AnchorSong(id="1", name="Anchor", artist="Artist")]
        result = DJSetCurator._remove_anchors(candidates, anchors)
        assert len(result) == 1


class TestApplyDiversity:
    def test_basic_split(self):
        curator = DJSetCurator.__new__(DJSetCurator)
        scored = [
            ScoredSong(song={"id": "1", "name": "A", "artist": "X"}, score=90),
            ScoredSong(song={"id": "2", "name": "B", "artist": "Y"}, score=80),
            ScoredSong(song={"id": "3", "name": "C", "artist": "Z"}, score=70),
            ScoredSong(song={"id": "4", "name": "D", "artist": "W"}, score=60),
        ]
        result = curator._apply_diversity(scored, target_count=3, diversity_ratio=0.33)
        assert len(result) == 3

    def test_fill_to_target(self):
        curator = DJSetCurator.__new__(DJSetCurator)
        scored = [
            ScoredSong(song={"id": "1", "name": "A", "artist": "X"}, score=90),
            ScoredSong(song={"id": "2", "name": "B", "artist": "Y"}, score=80),
        ]
        result = curator._apply_diversity(scored, target_count=5, diversity_ratio=0.5)
        assert len(result) == 2  # 不能超过可用数量

    def test_diversity_avoids_same_artist(self):
        curator = DJSetCurator.__new__(DJSetCurator)
        scored = [
            ScoredSong(song={"id": "1", "name": "A", "artist": "X"}, score=90),
            ScoredSong(song={"id": "2", "name": "B", "artist": "X"}, score=85),
            ScoredSong(song={"id": "3", "name": "C", "artist": "Y"}, score=70),
            ScoredSong(song={"id": "4", "name": "D", "artist": "Z"}, score=60),
        ]
        result = curator._apply_diversity(scored, target_count=3, diversity_ratio=0.33)
        # 多样性部分应优先选不同艺术家的
        artists = [s.song["artist"] for s in result]
        assert "Y" in artists or "Z" in artists


@pytest.mark.asyncio
class TestBuildPlaylist:
    async def test_empty_anchors(self):
        class FakeMCP:
            pass

        curator = DJSetCurator(mcp_client=FakeMCP())
        with pytest.raises(ValueError, match="至少"):
            await curator.build_playlist([], "Test", 10)

    async def test_successful_build(self):
        class FakeMCP:
            async def search_song(self, keyword):
                return [{"id": keyword, "name": f"Song {keyword}", "artist": "Artist"}]

            async def get_similar_songs(self, song_id, limit=20):
                return [
                    {"id": f"s{i}", "name": f"Similar {i}", "artist": f"Artist {i % 3}"}
                    for i in range(limit)
                ]

            async def create_playlist(self, name):
                return "playlist_123"

            async def add_tracks_to_playlist(self, playlist_id, track_ids):
                return "OK"

        curator = DJSetCurator(mcp_client=FakeMCP())
        result = await curator.build_playlist(
            anchor_queries=["111"],
            playlist_name="Test Set",
            target_count=5,
        )
        assert result["playlist_name"] == "Test Set"
        assert result["playlist_id"] == "playlist_123"
        assert result["stats"]["filtered_count"] == 5
        assert result["stats"]["total_candidates"] == 30
        assert isinstance(result["stats"]["avg_score"], float)

    async def test_no_candidates(self):
        class FakeMCP:
            async def search_song(self, keyword):
                return [{"id": keyword, "name": f"Song {keyword}", "artist": "Artist"}]

            async def get_similar_songs(self, song_id, limit=20):
                return []

        curator = DJSetCurator(mcp_client=FakeMCP())
        with pytest.raises(RuntimeError, match="未获取"):
            await curator.build_playlist(
                anchor_queries=["111"],
                playlist_name="Test",
                target_count=5,
            )
