"""级联扩展 - 用已选候选作为二级锚点获取更多推荐"""

import logging

from dj_set_curator.deduplicator import by_id, remove_anchors
from dj_set_curator.models import AnchorSong

logger = logging.getLogger(__name__)


async def expand(
    candidates: list[dict],
    anchors: list[AnchorSong],
    target_count: int,
    mcp_client,
    max_extra_anchors: int = 3,
    limit_per_anchor: int = 20,
) -> list[dict]:
    """级联扩展：用已选候选中的 top 歌曲作为二级锚点，获取更多相似推荐"""
    if len(candidates) >= target_count:
        return candidates

    extra_anchor_ids = set()
    extra_anchors = []
    used_artists = set()

    for song in candidates:
        sid = str(song.get("id", ""))
        artist = song.get("artist", "").lower()
        if sid in {a.id for a in anchors}:
            continue
        if artist in used_artists and len(extra_anchors) >= 2:
            continue
        if sid not in extra_anchor_ids:
            extra_anchor_ids.add(sid)
            extra_anchors.append(song)
            used_artists.add(artist)
            if len(extra_anchors) >= max_extra_anchors:
                break

    if not extra_anchors:
        logger.warning("无可用二级锚点进行扩展")
        return candidates

    logger.info(
        "候选池不足 (%d < %d)，启用级联扩展，使用 %d 个二级锚点",
        len(candidates), target_count, len(extra_anchors),
    )

    all_new = list(candidates)
    for song in extra_anchors:
        logger.info("获取二级锚点 '%s' 的相似推荐...", song.get("name", "未知"))
        similar = await mcp_client.get_similar_songs(str(song["id"]), limit=limit_per_anchor)
        logger.info("二级锚点 '%s' 获得 %d 首相似推荐", song.get("name", "未知"), len(similar))
        all_new.extend(similar)

    unique = by_id(all_new)
    unique = remove_anchors(unique, anchors)
    logger.info("级联扩展后候选歌曲: %d 首", len(unique))
    return unique
