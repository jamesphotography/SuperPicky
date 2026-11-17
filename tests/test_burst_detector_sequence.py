# -*- coding: utf-8 -*-
"""
连拍分组单测：相机连拍序号优先，没有序号时按时间 / Burst grouping: camera sequence number first, time otherwise.

起因：ILCE-7M4 无损压缩 RAW 连拍约 6 张/秒（帧间隔 166 ms），「连拍速度」默认 10 张/秒
（阈值 100 ms），1,779 张连拍素材一组都没分出来。

Why: an ILCE-7M4 shooting lossless RAW bursts at about 6 fps (166 ms between frames); with the
default "burst speed" of 10 fps (100 ms threshold) 1,779 burst frames produced no group at all.
"""
from typing import List, Optional

from core.burst_detector import BurstDetector, PhotoTimestamp

SONY = "SONY|ILCE-7M4|1"


def detector(threshold_ms: int = 100) -> BurstDetector:
    """固定阈值、不做 pHash 的检测器 / A detector with a fixed threshold and no pHash."""
    d = BurstDetector(exiftool_path="exiftool", use_phash=False)
    d.TIME_THRESHOLD_MS = threshold_ms
    d.MIN_BURST_COUNT = 4
    return d


def frames(times_ms: List[int], sequences: List[Optional[int]], camera: str = SONY) -> List[PhotoTimestamp]:
    """按毫秒时间和序号造一串帧 / Build frames from millisecond times and sequence numbers."""
    return [PhotoTimestamp(filepath=f"{camera}-{t}", precise_time=t / 1000, camera=camera, sequence=s)
            for t, s in zip(times_ms, sequences)]


def sizes(d: BurstDetector, photos: List[PhotoTimestamp]) -> List[int]:
    """各连拍组的张数 / Photo count of each burst group."""
    return [g.count for g in d.detect_groups_by_time_only(photos)]


def test_sony_6fps_burst_is_found_with_default_10fps_setting():
    """166 ms 间隔超过 100 ms 阈值，但序号 1..6 连续，仍是一组。"""
    assert sizes(detector(), frames([0, 166, 332, 498, 664, 830], [1, 2, 3, 4, 5, 6])) == [6]


def test_sequence_reset_starts_a_new_burst():
    """两次连拍只隔 276 ms（实拍最小值），序号回到 1 就断开。
    阈值取界面能设的最大值 250 ms（最低 4 张/秒）：间隔在时间阈值内的一律同组（见下一条），
    所以序号重置只在间隔超过阈值时才断开。"""
    times = [0, 166, 332, 498, 774, 940, 1106, 1272]
    assert sizes(detector(threshold_ms=250), frames(times, [1, 2, 3, 4, 1, 2, 3, 4])) == [4, 4]


def test_frames_deleted_in_camera_keep_the_burst_together():
    """机内删掉第 3、4 张：间隔变成 498 ms，序号仍递增。"""
    assert sizes(detector(), frames([0, 166, 664, 830], [1, 2, 5, 6])) == [4]


def test_single_shots_never_group():
    """序号 0 是单张；正常的单张间隔（这里 400 ms）不成组。"""
    assert sizes(detector(), frames([0, 400, 800, 1200], [0, 0, 0, 0])) == []


def test_time_rule_still_groups_frames_labelled_single():
    """标成单张（0）但间隔在时间阈值内的帧照样成组（#168 跟进）。

    人手不可能在 100 ms 量级内连按快门，这么近的「单张」只能是相机把连拍帧标成了 0；
    序号只能多认出连拍，不能把原来按时间能分出的组拆掉。"""
    assert sizes(detector(), frames([0, 50, 100, 150], [0, 0, 0, 0])) == [4]


def test_sequence_gap_over_one_second_splits():
    """序号递增但隔了 1 秒以上，不算同一次连拍。"""
    assert sizes(detector(), frames([0, 166, 332, 498, 1600, 1766, 1932, 2098], [1, 2, 3, 4, 5, 6, 7, 8])) == [4, 4]


def test_without_sequence_the_time_threshold_still_decides():
    """没有序号（未登记的厂商）：行为和以前一样，只看时间阈值。"""
    no_seq = [None] * 5
    assert sizes(detector(100), frames([0, 90, 180, 270, 360], no_seq, "NIKON|Z 9|1")) == [5]
    assert sizes(detector(100), frames([0, 166, 332, 498, 664], no_seq, "NIKON|Z 9|1")) == []


def test_two_bodies_firing_at_once_each_get_their_burst():
    """两台机身同时连拍、时间交错：各自成组，互不打断。"""
    a = frames([0, 332, 664, 996], [1, 2, 3, 4], "SONY|ILCE-7M4|1")
    b = frames([166, 498, 830, 1162, 1494], [1, 2, 3, 4, 5], "SONY|ILCE-1|2")
    assert sorted(sizes(detector(), a + b)) == [4, 5]


def test_same_frame_derivative_does_not_cut_the_burst():
    """-Enhanced-NR.dng 和原片的时间、序号都相同：跟原片同组，后面的帧接着算。"""
    photos = frames([0, 166, 332, 498, 664], [1, 2, 3, 4, 5])
    photos.insert(3, PhotoTimestamp(filepath="dup.dng", precise_time=0.332, camera=SONY, sequence=3))
    assert sizes(detector(), photos) == [6]


def test_single_shot_before_a_burst_stays_out():
    """单张（序号 0）后 300 ms 开始连拍：单张不并进去。"""
    assert sizes(detector(), frames([0, 300, 466, 632, 798], [0, 1, 2, 3, 4])) == [4]


def test_only_sony_reads_the_sequence_tag():
    """Canon 等厂商的同名标签尚未在实拍上核对，先不采用。"""
    d = detector()
    sony = {"SourceFile": "a.ARW", "DateTimeOriginal": "2026:11:15 10:20:57", "SubSecTimeOriginal": "101",
            "Make": "SONY", "Model": "ILCE-7M4", "SequenceNumber": 4}
    single = dict(sony, SequenceNumber="Single")
    canon = dict(sony, Make="Canon", Model="EOS R5", SequenceNumber=4)
    old = {k: v for k, v in sony.items() if k != "SequenceNumber"}
    parsed = d._parse_exif_timestamps([sony, single, canon, old])
    assert [p.sequence for p in parsed] == [4, 0, None, None]
    assert parsed[0].camera == "SONY|ILCE-7M4|"


def test_gap_stats_suggests_slower_speed_for_6fps_without_sequence():
    """没有序号的 6 张/秒机身：0 组，统计给出中位间隔并建议调低连拍速度（#168 跟进）。"""
    d = detector()
    photos = frames([i * 166 for i in range(12)], [None] * 12, camera="NIKON CORPORATION|Z 6_2|1")
    assert sizes(d, photos) == []
    stats = d.gap_stats(photos)
    assert stats["threshold_ms"] == 100 and stats["median_gap_ms"] == 166
    assert stats["with_sequence"] == 0 and stats["slow_pairs"] == 11 and stats["suggest_slower"]


def test_gap_stats_no_hint_when_gaps_are_far_apart():
    """单张之间隔得远（> 400 ms）不提示。"""
    d = detector()
    stats = d.gap_stats(frames([i * 900 for i in range(12)], [None] * 12, camera="X|Y|1"))
    assert stats["slow_pairs"] == 0 and not stats["suggest_slower"]
