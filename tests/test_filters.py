"""测试筛选排序引擎"""

import pytest

from dj_set_curator.anchor import AnchorSong
from dj_set_curator.filters import SongFilter, CAMELOT_WHEEL
from dj_set_curator.models import ScoredSong, Song


class TestBPMScore:
    def test_exact_match(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(128.0, [128.0]) == 100.0

    def test_within_tolerance(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(130.0, [128.0]) == 80.0  # diff=2, 80% of 50 range -> actually 100 - (2/5)*50 = 80

    def test_close_to_full(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(129.0, [128.0]) == 100.0  # diff=1 <= 1.0 -> perfect match

    def test_far_away(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(140.0, [120.0]) == 0.0  # diff=20

    def test_missing_bpm(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(None, [128.0]) == 50.0

    def test_no_anchor_bpms(self):
        f = SongFilter(bpm_tolerance=5)
        assert f._bpm_score(128.0, []) == 50.0


class TestKeyDistance:
    def test_same_key(self):
        f = SongFilter()
        assert f._key_distance("8B", "8B") == 0

    def test_ab_swap(self):
        f = SongFilter()
        assert f._key_distance("8B", "8A") == 0

    def test_adjacent_same_letter(self):
        f = SongFilter()
        assert f._key_distance("8B", "9B") == 1

    def test_adjacent_different_letter(self):
        f = SongFilter()
        # 8B vs 9A: different letters, diff=1 -> 2
        assert f._key_distance("8B", "9A") == 2

    def test_ring_wrap(self):
        f = SongFilter()
        # 1B vs 12B: ring diff = 1
        assert f._key_distance("1B", "12B") == 1

    def test_far_key(self):
        f = SongFilter()
        assert f._key_distance("1B", "6B") == 3

    def test_unknown_key(self):
        f = SongFilter()
        assert f._key_distance(None, "8B") == 999

    def test_full_wheel_coverage(self):
        f = SongFilter()
        for key, camelot in CAMELOT_WHEEL.items():
            assert f._normalize_key(key) == camelot
            assert f._normalize_key(camelot.lower()) == camelot


class TestKeyScore:
    def test_perfect_match(self):
        f = SongFilter()
        assert f._key_score("8B", ["8B"]) == 100.0

    def test_compatible(self):
        f = SongFilter()
        assert f._key_score("8B", ["9B"]) == 85.0

    def test_no_key_data(self):
        f = SongFilter()
        assert f._key_score(None, ["8B"]) == 50.0


class TestArtistScore:
    def test_exact_match(self):
        f = SongFilter()
        candidate = Song(id="1", name="x", artist="Aphex Twin")
        anchors = [AnchorSong(id="1", name="x", artist="Aphex Twin")]
        assert f._artist_score(candidate, anchors) == 100.0

    def test_substring_match(self):
        f = SongFilter()
        candidate = Song(id="1", name="x", artist="Aphex Twin & Squarepusher")
        anchors = [AnchorSong(id="1", name="x", artist="Aphex Twin")]
        assert f._artist_score(candidate, anchors) == 100.0

    def test_no_match(self):
        f = SongFilter()
        candidate = Song(id="1", name="x", artist="Boards of Canada")
        anchors = [AnchorSong(id="1", name="x", artist="Aphex Twin")]
        assert f._artist_score(candidate, anchors) == 30.0


class TestScoreCandidates:
    def test_basic_scoring(self):
        f = SongFilter()
        candidates = [
            Song(id="1", name="Song A", artist="Artist X"),
            Song(id="2", name="Song B", artist="Artist Y"),
        ]
        anchors = [AnchorSong(id="99", name="Anchor", artist="Artist X")]
        scored = f.score_candidates(candidates, anchors)
        assert len(scored) == 2
        # 第一个应该分数更高（同艺术家）
        assert scored[0].song.id == "1"
        assert scored[0].score > scored[1].score

    def test_score_bounds(self):
        f = SongFilter()
        candidates = [Song(id="1", name="Song", artist="Artist")]
        anchors = [AnchorSong(id="99", name="Anchor", artist="Other")]
        scored = f.score_candidates(candidates, anchors)
        assert 0 <= scored[0].score <= 100


class TestDiversityScore:
    def test_unique_song(self):
        f = SongFilter()
        candidate = Song(id="1", name="New Song", artist="New Artist")
        assert f._diversity_score(candidate, []) == 100.0

    def test_duplicate_name(self):
        f = SongFilter()
        candidate = Song(id="1", name="Same Song", artist="Artist B")
        selected = [ScoredSong(song=Song(id="2", name="Same Song", artist="Artist A"), score=50)]
        assert f._diversity_score(candidate, selected) == 10.0

    def test_same_artist(self):
        f = SongFilter()
        candidate = Song(id="1", name="Different Song", artist="Same Artist")
        selected = [ScoredSong(song=Song(id="2", name="Other Song", artist="Same Artist"), score=50)]
        assert f._diversity_score(candidate, selected) == 50.0
