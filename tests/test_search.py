"""채팅 키워드 검색 테스트.

검색은 "찾았다/못 찾았다" 가 눈에 바로 보이는 기능이라 조용히 어긋나기 어렵지만,
시간축 변환(VOD → 로컬 녹화)만은 예외다. offset 을 빼먹거나 부호를 뒤집으면
결과가 그럴싸한 시각으로 나오고, 미리보기를 눌러 봐야 어긋난 걸 안다.
여기서는 그 변환과 묶는 규칙을 못 박아 둔다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from livehl import search                                    # noqa: E402


def ev(t, text, author="시청자", kind="text", source="youtube"):
    return {"t": float(t), "text": text, "author": author, "kind": kind, "source": source}


# --------------------------------------------------------------------- 검색어 해석

def test_query_splits_on_spaces_and_commas():
    assert search.parse_query("롤 발로란트") == ["롤", "발로란트"]
    assert search.parse_query("롤, 발로란트") == ["롤", "발로란트"]


def test_quoted_phrase_stays_together():
    assert search.parse_query('"그 게임" 롤') == ["그 게임", "롤"]


def test_duplicate_words_are_dropped():
    assert search.parse_query("롤 롤 LOL") == ["롤", "lol"]


def test_empty_query_finds_nothing():
    """빈 검색어로 전체가 걸리면 안 된다."""
    out = search.find([ev(10, "아무말")], "   ")
    assert out["groups"] == [] and out["total"] == 0


# --------------------------------------------------------------------- 찾기

def test_finds_only_matching_messages():
    events = [ev(10, "롤 하자"), ev(12, "밥 먹자"), ev(14, "롤 ㄱㄱ")]
    out = search.find(events, "롤", pre=0, post=0)
    assert out["total"] == 2
    assert len(out["groups"]) == 1
    assert out["groups"][0]["hits"] == 2


def test_english_search_ignores_case():
    events = [ev(10, "LOL 하자"), ev(12, "lol ㅋㅋ")]
    out = search.find(events, "Lol", pre=0, post=0)
    assert out["total"] == 2


def test_matches_inside_a_longer_word():
    """한국어는 조사가 붙는다. '롤' 로 '롤을' 을 찾지 못하면 쓸모가 없다."""
    out = search.find([ev(10, "롤을 하자")], "롤", pre=0, post=0)
    assert out["total"] == 1


def test_any_keyword_matches():
    events = [ev(10, "롤 하자"), ev(200, "발로란트 하자")]
    out = search.find(events, "롤 발로란트", pre=0, post=0)
    assert out["total"] == 2
    assert len(out["groups"]) == 2      # 시간이 멀면 따로 묶인다


# --------------------------------------------------------------------- 묶기

def test_nearby_hits_become_one_group():
    events = [ev(t, "롤") for t in (100, 110, 120)]
    out = search.find(events, "롤", gap=30, pre=0, post=0)
    assert len(out["groups"]) == 1
    assert out["groups"][0]["hits"] == 3


def test_far_apart_hits_split():
    events = [ev(100, "롤"), ev(400, "롤")]
    out = search.find(events, "롤", gap=30, pre=0, post=0)
    assert len(out["groups"]) == 2


def test_group_spans_first_to_last_hit_with_padding():
    events = [ev(100, "롤"), ev(120, "롤")]
    out = search.find(events, "롤", gap=30, pre=15, post=10)
    g = out["groups"][0]
    assert g["start"] == 85.0 and g["end"] == 130.0
    assert g["dur"] == 45.0


def test_padding_does_not_go_below_zero():
    out = search.find([ev(3, "롤")], "롤", pre=15, post=10)
    assert out["groups"][0]["start"] == 0.0


def test_group_does_not_run_past_the_end_of_the_video():
    out = search.find([ev(90, "롤")], "롤", duration=95.0, pre=0, post=30)
    assert out["groups"][0]["end"] == 95.0


def test_min_hits_filters_one_off_mentions():
    """한 번 지나가듯 나온 단어는 구간이 아니다."""
    events = [ev(10, "롤")] + [ev(t, "롤") for t in (200, 205, 210)]
    out = search.find(events, "롤", gap=30, min_hits=2)
    assert len(out["groups"]) == 1
    assert out["groups"][0]["hits"] == 3


# --------------------------------------------------------------------- 시간축

def test_times_are_shifted_into_recording_time():
    """채팅은 VOD 시간, 후보 구간은 녹화 시간이다. offset 을 더해서 돌려줘야
    미리보기·XML 이 맞는 자리를 가리킨다."""
    out = search.find([ev(100, "롤")], "롤", offset=42.0, pre=0, post=0)
    g = out["groups"][0]
    assert g["start"] == 142.0 and g["end"] == 142.0
    assert g["messages"][0]["t"] == 142.0


def test_negative_offset_works_too():
    out = search.find([ev(100, "롤")], "롤", offset=-30.0, pre=0, post=0)
    assert out["groups"][0]["start"] == 70.0


# --------------------------------------------------------------------- 결과 내용

def test_hit_counts_per_keyword_are_reported():
    events = [ev(10, "롤"), ev(12, "롤"), ev(14, "발로란트")]
    out = search.find(events, "롤 발로란트", gap=30)
    assert out["groups"][0]["matched"] == {"롤": 2, "발로란트": 1}


def test_repeated_messages_are_shown_once():
    events = [ev(10 + i, "롤 ㄱㄱ") for i in range(10)]
    out = search.find(events, "롤", gap=30, samples=5)
    assert len(out["groups"][0]["messages"]) == 1


def test_both_platforms_are_counted():
    events = [ev(10, "롤", source="youtube"), ev(12, "롤", source="chzzk")]
    out = search.find(events, "롤", gap=30)
    assert out["groups"][0]["sources"] == {"youtube": 1, "chzzk": 1}


def test_groups_come_back_in_time_order():
    events = [ev(500, "롤"), ev(505, "롤"), ev(510, "롤"), ev(100, "롤")]
    out = search.find(events, "롤", gap=30)
    starts = [g["start"] for g in out["groups"]]
    assert starts == sorted(starts)


def test_limit_keeps_the_busiest_groups():
    events = [ev(100, "롤")] + [ev(500 + i, "롤") for i in range(5)]
    out = search.find(events, "롤", gap=30, limit=1)
    assert len(out["groups"]) == 1
    assert out["groups"][0]["hits"] == 5      # 한 번짜리가 아니라 몰린 쪽을 남긴다


def test_peak_sits_inside_the_group():
    events = [ev(t, "롤") for t in (100, 102, 104, 300)]
    out = search.find(events, "롤", gap=300, pre=0, post=0)
    g = out["groups"][0]
    assert g["start"] <= g["peak"] <= g["end"]


# --------------------------------------------------------------------- 후보로 바꾸기

def test_converted_segments_carry_the_search_mark():
    """다시 검출해도 살아남으려면 이 표시가 있어야 한다."""
    out = search.find([ev(10, "롤"), ev(12, "롤")], "롤")
    segs = search.to_segments(out["groups"], out["query"])
    assert segs[0]["src"] == "search"
    assert segs[0]["selected"] is True
    assert "롤" in segs[0]["label"]
    assert "2회" in segs[0]["reason"]


def test_converted_segments_keep_the_sample_messages():
    out = search.find([ev(10, "롤 하자")], "롤")
    segs = search.to_segments(out["groups"], out["query"])
    assert segs[0]["chat"][0]["text"] == "롤 하자"


# --------------------------------------------------------------------- 겹침 판정

@pytest.mark.parametrize("a, b, expected", [
    ((0, 100), (50, 150), True),      # 절반 겹침
    ((0, 100), (90, 200), False),     # 살짝만 스침
    ((0, 100), (100, 200), False),    # 딱 붙음
    ((0, 100), (10, 20), True),       # 짧은 쪽이 안에 완전히 들어감
    ((0, 100), (200, 300), False),
])
def test_overlap_rule(a, b, expected):
    seg = lambda s, e: {"start": float(s), "end": float(e)}
    assert search.overlaps(seg(*a), seg(*b)) is expected
