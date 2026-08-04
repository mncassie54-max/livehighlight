"""프리미어 프로 임포트용 FCP7 XML (.xml) 생성.

두 가지 모드:
  markers  : 원본 전체가 1개 클립으로 놓인 시퀀스 + 후보 지점마다 시퀀스 마커
             → 프리미어에서 마커 패널 보면서 직접 컷
  roughcut : 후보 구간만 순서대로 이어붙인 러프컷 시퀀스
             → 바로 다듬어서 완성

프리미어는 .mkv 를 임포트하지 못한다. 원본이 mkv 면 먼저 mp4 로 리먹스해서
그 경로를 XML 에 넣어야 한다 (server 에서 처리).
"""

import os
import urllib.parse
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

_TC_FMT = "%02d:%02d:%02d:%02d"


def _rate(tb: int, ntsc: bool) -> str:
    return "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (tb, "TRUE" if ntsc else "FALSE")


def _pathurl(path: str) -> str:
    p = os.path.abspath(path)
    return "file://localhost" + urllib.parse.quote(p)


def _frames(sec: float, fps: float) -> int:
    return int(round(float(sec) * float(fps)))


def timecode(sec: float, tb: int, ntsc: bool) -> str:
    f = int(round(sec * tb))
    return _TC_FMT % (f // (3600 * tb), (f // (60 * tb)) % 60, (f // tb) % 60, f % tb)


def _file_element(path: str, media: Dict[str, Any], total_frames: int, rate: str, first: bool) -> str:
    if not first:
        return '<file id="file-1"/>'
    w = media.get("width") or 1920
    h = media.get("height") or 1080
    astreams = media.get("audio_streams") or []
    ch = (astreams[0].get("channels") if astreams else 2) or 2
    sr = (astreams[0].get("sample_rate") if astreams else 48000) or 48000
    return (
        '<file id="file-1">'
        "<name>%s</name>"
        "<pathurl>%s</pathurl>"
        "%s"
        "<duration>%d</duration>"
        "<timecode>%s<string>00:00:00:00</string><frame>0</frame>"
        "<displayformat>NDF</displayformat></timecode>"
        "<media><video><samplecharacteristics>%s"
        "<width>%d</width><height>%d</height>"
        "<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>"
        "<fielddominance>none</fielddominance>"
        "</samplecharacteristics></video>"
        "<audio><samplecharacteristics><depth>16</depth><samplerate>%d</samplerate>"
        "</samplecharacteristics><channelcount>%d</channelcount></audio></media>"
        "</file>"
        % (
            escape(os.path.basename(path)),
            escape(_pathurl(path)),
            rate,
            total_frames,
            rate,
            rate,
            w,
            h,
            sr,
            min(int(ch), 2),
        )
    )


def _clipitem(
    cid: str,
    name: str,
    rate: str,
    seq_start: int,
    seq_end: int,
    src_in: int,
    src_out: int,
    file_el: str,
    total_frames: int,
    audio_track: Optional[int] = None,
    links: Optional[List[str]] = None,
) -> str:
    src = ""
    if audio_track is not None:
        src = (
            "<sourcetrack><mediatype>audio</mediatype><trackindex>%d</trackindex></sourcetrack>"
            % audio_track
        )
    link_xml = ""
    if links:
        for lid in links:
            mt = "audio" if "-a" in lid else "video"
            link_xml += (
                "<link><linkclipref>%s</linkclipref><mediatype>%s</mediatype>"
                "<trackindex>1</trackindex></link>" % (escape(lid), mt)
            )
    return (
        '<clipitem id="%s"><masterclipid>master-1</masterclipid>'
        "<name>%s</name><enabled>TRUE</enabled><duration>%d</duration>%s"
        "<start>%d</start><end>%d</end><in>%d</in><out>%d</out>"
        "%s%s%s</clipitem>"
        % (
            escape(cid),
            escape(name),
            total_frames,
            rate,
            seq_start,
            seq_end,
            src_in,
            src_out,
            file_el,
            src,
            link_xml,
        )
    )


def build_xml(
    video_path: str,
    media: Dict[str, Any],
    segments: List[Dict[str, Any]],
    mode: str = "markers",
    sequence_name: str = "livehl",
    audio_tracks: Optional[List[int]] = None,
) -> str:
    """mode: markers | roughcut"""
    fr = media.get("fps_rational") or {"timebase": 30, "ntsc": False, "exact": 30.0}
    tb, ntsc, fps = int(fr["timebase"]), bool(fr["ntsc"]), float(fr["exact"])
    rate = _rate(tb, ntsc)
    duration = float(media.get("duration") or 0.0)
    total_frames = max(_frames(duration, fps), 1)
    w = media.get("width") or 1920
    h = media.get("height") or 1080
    astreams = media.get("audio_streams") or []
    if audio_tracks is None:
        audio_tracks = [1] if not astreams else list(range(1, min(len(astreams), 4) + 1))
    if not audio_tracks:
        audio_tracks = [1]

    video_items: List[str] = []
    audio_items: Dict[int, List[str]] = {t: [] for t in audio_tracks}
    markers: List[str] = []
    first_file = [True]

    def file_el() -> str:
        el = _file_element(video_path, media, total_frames, rate, first_file[0])
        first_file[0] = False
        return el

    base_name = os.path.basename(video_path)

    if mode == "roughcut":
        cursor = 0
        for i, s in enumerate(segments, start=1):
            si = _frames(s["start"], fps)
            so = _frames(s["end"], fps)
            if so <= si:
                continue
            length = so - si
            vid_id = "clip-%d" % i
            aud_ids = ["clip-%d-a%d" % (i, t) for t in audio_tracks]
            name = s.get("title") or "%s %s" % (s.get("label", ""), _mmss(s["start"]))
            video_items.append(
                _clipitem(vid_id, name, rate, cursor, cursor + length, si, so, file_el(),
                          total_frames, None, aud_ids)
            )
            for t, aid in zip(audio_tracks, aud_ids):
                audio_items[t].append(
                    _clipitem(aid, name, rate, cursor, cursor + length, si, so, file_el(),
                              total_frames, t, [vid_id])
                )
            markers.append(
                "<marker><name>%s</name><comment>%s</comment><in>%d</in><out>-1</out></marker>"
                % (escape(name), escape(_comment(s)), cursor)
            )
            cursor += length
        seq_frames = max(cursor, 1)
    else:
        vid_id = "clip-1"
        aud_ids = ["clip-1-a%d" % t for t in audio_tracks]
        video_items.append(
            _clipitem(vid_id, base_name, rate, 0, total_frames, 0, total_frames, file_el(),
                      total_frames, None, aud_ids)
        )
        for t, aid in zip(audio_tracks, aud_ids):
            audio_items[t].append(
                _clipitem(aid, base_name, rate, 0, total_frames, 0, total_frames, file_el(),
                          total_frames, t, [vid_id])
            )
        for s in segments:
            name = s.get("title") or "%s #%d %s" % (s.get("label", ""), s.get("rank", 0), _mmss(s["start"]))
            markers.append(
                "<marker><name>%s</name><comment>%s</comment><in>%d</in><out>%d</out></marker>"
                % (
                    escape(name),
                    escape(_comment(s)),
                    _frames(s["start"], fps),
                    _frames(s["end"], fps),
                )
            )
        seq_frames = total_frames

    audio_track_xml = "".join(
        "<track>%s<enabled>TRUE</enabled><locked>FALSE</locked></track>" % "".join(items)
        for items in audio_items.values()
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">\n'
        '<sequence id="livehl-seq">'
        "<name>%s</name><duration>%d</duration>%s"
        "<timecode>%s<string>00:00:00:00</string><frame>0</frame>"
        "<displayformat>NDF</displayformat></timecode>"
        "<media>"
        "<video><format><samplecharacteristics>%s<width>%d</width><height>%d</height>"
        "<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>"
        "<fielddominance>none</fielddominance></samplecharacteristics></format>"
        "<track>%s<enabled>TRUE</enabled><locked>FALSE</locked></track></video>"
        "<audio><numOutputChannels>2</numOutputChannels>"
        "<format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
        "</samplecharacteristics></format>%s</audio>"
        "</media>%s</sequence>\n</xmeml>\n"
        % (
            escape(sequence_name),
            seq_frames,
            rate,
            rate,
            rate,
            w,
            h,
            "".join(video_items),
            audio_track_xml,
            "".join(markers),
        )
    )


def _mmss(sec: float) -> str:
    sec = int(sec)
    if sec >= 3600:
        return "%d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)
    return "%d:%02d" % (sec // 60, sec % 60)


def _comment(s: Dict[str, Any]) -> str:
    bits = ["점수 %.2f" % s.get("score", 0)]
    if s.get("reason"):
        bits.append(s["reason"])
    chats = s.get("chat") or []
    if chats:
        bits.append("채팅: " + " / ".join((c.get("text") or "")[:24] for c in chats[:3]))
    return " | ".join(bits)


# --------------------------------------------------------------------------- 리포트


def write_csv(path: str, segments: List[Dict[str, Any]], media: Dict[str, Any]) -> str:
    import csv

    fr = media.get("fps_rational") or {"timebase": 30, "ntsc": False}
    tb, ntsc = int(fr["timebase"]), bool(fr["ntsc"])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["순위", "라벨", "시작(TC)", "끝(TC)", "시작(초)", "길이(초)", "점수", "근거", "대표 채팅"])
        for s in segments:
            wr.writerow(
                [
                    s.get("rank"),
                    s.get("label"),
                    timecode(s["start"], tb, ntsc),
                    timecode(s["end"], tb, ntsc),
                    round(s["start"], 1),
                    s.get("dur"),
                    s.get("score"),
                    s.get("reason"),
                    " / ".join((c.get("text") or "")[:30] for c in (s.get("chat") or [])[:5]),
                ]
            )
    return path


def write_txt(path: str, segments: List[Dict[str, Any]], media: Dict[str, Any], name: str) -> str:
    fr = media.get("fps_rational") or {"timebase": 30, "ntsc": False}
    tb, ntsc = int(fr["timebase"]), bool(fr["ntsc"])
    lines = ["# %s — 하이라이트 후보 %d개" % (name, len(segments)), ""]
    for s in segments:
        lines.append(
            "%2d) %s  %s ~ %s (%.0f초)  점수 %.2f" % (
                s.get("rank", 0), s.get("label", ""),
                timecode(s["start"], tb, ntsc), timecode(s["end"], tb, ntsc),
                s.get("dur", 0), s.get("score", 0),
            )
        )
        if s.get("reason"):
            lines.append("      근거: %s" % s["reason"])
        for c in (s.get("chat") or [])[:4]:
            lines.append("      💬 %s" % (c.get("text") or "")[:70])
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
