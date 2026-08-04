"""분석 → 정렬 → 검출 → 익스포트 오케스트레이션."""

import json
import os
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import align, audio, chat, config, export_clips, ffmpeg_tools, score, store
from . import export_xml as xmlout

Progress = Callable[[float, str], None]


def _noop(frac: float, msg: str = "") -> None:
    pass


# --------------------------------------------------------------------------- 신호 캐시


def signals_path(pid: str) -> str:
    return os.path.join(store.project_dir(pid), "signals.npz")


def events_path(pid: str) -> str:
    return os.path.join(store.project_dir(pid), "chat_events.json")


def load_signals(pid: str) -> Dict[str, np.ndarray]:
    p = signals_path(pid)
    if not os.path.exists(p):
        return {}
    with np.load(p) as z:
        return {k: z[k] for k in z.files}


def save_signals(pid: str, sig: Dict[str, np.ndarray]) -> None:
    np.savez_compressed(signals_path(pid), **{k: np.asarray(v, dtype=np.float32) for k, v in sig.items()})


def load_events(pid: str) -> List[Dict[str, Any]]:
    p = events_path(pid)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- 1. 프로브


def probe_project(proj: Dict[str, Any]) -> Dict[str, Any]:
    media = ffmpeg_tools.probe(proj["video_path"])
    proj["media"] = media
    astreams = media.get("audio_streams") or []
    if proj.get("mic_stream") is None and astreams:
        # 트랙이 여러 개면 보통 1번=믹스, 2번=마이크 (OBS 기본)
        mic = astreams[1] if len(astreams) > 1 else astreams[0]
        proj["mic_stream"] = mic["index"]
    if proj.get("mix_stream") is None and astreams:
        proj["mix_stream"] = astreams[0]["index"]
    vstart, src = align.file_start_time(proj["video_path"], media.get("duration"))
    proj["video_start_utc"] = vstart
    proj["video_start_source"] = src
    store.save(proj)
    return media


# --------------------------------------------------------------------------- 2. 분석


def analyze(
    pid: str,
    do_chat: bool = True,
    do_audio: bool = True,
    auto_align: bool = True,
    progress: Progress = _noop,
) -> Dict[str, Any]:
    proj = store.load(pid)
    if not proj.get("media"):
        progress(0.02, "미디어 정보 확인 중…")
        probe_project(proj)
        proj = store.load(pid)
    media = proj["media"]
    duration = float(media.get("duration") or 0.0)
    n_local = int(duration) + 1
    sig = load_signals(pid)

    # ---- 채팅
    if do_chat and proj.get("youtube_url"):
        progress(0.05, "유튜브 채팅 리플레이 받는 중…")
        meta = chat.fetch(
            proj["youtube_url"],
            store.project_dir(pid),
            lambda f, m: progress(0.05 + 0.15 * f, m),
        )
        proj["chat_meta"] = meta
        proj["stream_start_utc"] = meta.get("release_timestamp") or meta.get("timestamp")
        events = chat.parse(meta.get("chat_file"), lambda f, m: progress(0.2 + 0.1 * f, m))
        with open(events_path(pid), "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False)
        vod_len = int(max(meta.get("duration") or 0, events[-1]["t"] if events else 0)) + 1
        cur = chat.curves(events, vod_len)
        sig.update(cur)
        proj["chat_stats"] = chat.stats(events)
        proj["vod_length"] = vod_len
        store.log(proj, "채팅 %d개 수집" % len(events))
        store.save(proj)

    # ---- 오디오
    if do_audio:
        mic = proj.get("mic_stream")
        if mic is None:
            raise RuntimeError("오디오 트랙이 없습니다.")
        progress(0.32, "마이크 트랙 분석 시작…")
        res = audio.analyze(
            proj["video_path"], int(mic), duration,
            lambda f, m: progress(0.32 + 0.55 * f, m),
        )
        sig.update(res["features"])
        proj["audio_stats"] = res["stats"]
        store.log(proj, "오디오 분석 완료 (트랙 index %s)" % mic)
        store.save(proj)

    save_signals(pid, sig)

    # ---- 정렬
    if proj.get("stream_start_utc"):
        init = align.initial_offset(proj["video_path"], duration, proj["stream_start_utc"])
        proj["offset_sec"] = init["offset_sec"]
        proj["offset_source"] = init["source"]
        store.save(proj)
    if auto_align and "chat_rate" in sig and "mic_excite" in sig:
        progress(0.9, "채팅↔오디오 시간축 미세보정 중…")
        r = align.refine_offset(sig["chat_rate"], sig["mic_excite"][:n_local], proj.get("offset_sec") or 0.0)
        if r.get("confidence", 0) >= 3.0:
            proj["offset_sec"] = r["offset_sec"]
            proj["offset_source"] = (proj.get("offset_source") or "") + " + 교차상관 보정(%+.0f초)" % r["delta"]
        proj["offset_refine"] = {k: r[k] for k in ("offset_sec", "delta", "confidence", "note") if k in r}
        store.save(proj)

    progress(0.95, "하이라이트 검출 중…")
    segs = detect(pid, progress=lambda f, m: progress(0.95 + 0.05 * f, m))
    return {"segments": len(segs), "offset_sec": proj.get("offset_sec"), "chat": proj.get("chat_stats")}


def refine(pid: str) -> Dict[str, Any]:
    proj = store.load(pid)
    sig = load_signals(pid)
    if "chat_rate" not in sig or "mic_excite" not in sig:
        raise RuntimeError("채팅과 오디오 신호가 모두 있어야 자동 보정이 가능합니다.")
    n_local = int(float(proj["media"]["duration"])) + 1
    r = align.refine_offset(sig["chat_rate"], sig["mic_excite"][:n_local], proj.get("offset_sec") or 0.0)
    proj["offset_refine"] = r
    if r.get("confidence", 0) >= 2.0:
        proj["offset_sec"] = r["offset_sec"]
        proj["offset_source"] = "교차상관 자동 보정 (신뢰도 %.1f)" % r["confidence"]
    store.save(proj)
    return r


# --------------------------------------------------------------------------- 3. 검출


def detect(pid: str, params: Optional[Dict[str, Any]] = None,
           weights: Optional[Dict[str, float]] = None, progress: Progress = _noop) -> List[Dict[str, Any]]:
    proj = store.load(pid)
    if params:
        proj["detect"].update(params)
    if weights:
        proj["weights"].update(weights)
    sig = load_signals(pid)
    if not sig:
        raise RuntimeError("먼저 분석을 실행하세요.")
    duration = float(proj["media"]["duration"])
    n_local = int(duration) + 1
    fused = score.build(sig, float(proj.get("offset_sec") or 0.0), n_local, proj["weights"])
    segs = score.detect(fused, proj["detect"], duration, proj["weights"])

    events = load_events(pid)
    if events:
        off = float(proj.get("offset_sec") or 0.0)
        for s in segs:
            # 로컬 시간 → VOD 시간으로 되돌려서 채팅을 뽑고,
            # 표시용 t 는 다시 로컬(녹화) 시간으로 바꿔서 후보 타임코드와 맞춘다
            msgs = chat.sample_messages(events, s["start"] - off, s["end"] - off, 8)
            for m in msgs:
                m["t_vod"] = m["t"]
                m["t"] = round(m["t"] + off, 1)
            s["chat"] = msgs
    else:
        for s in segs:
            s["chat"] = []

    # 이전 선택/제목 유지 (시작시각이 5초 이내면 같은 후보로 본다)
    old = {round(o["start"] / 5.0): o for o in (proj.get("segments") or [])}
    for s in segs:
        o = old.get(round(s["start"] / 5.0))
        if o:
            s["selected"] = o.get("selected", True)
            s["title"] = o.get("title", "")

    proj["segments"] = segs
    store.save(proj)
    progress(1.0, "후보 %d개" % len(segs))
    return segs


def curves_for_plot(pid: str, width: int = 1600) -> Dict[str, Any]:
    proj = store.load(pid)
    sig = load_signals(pid)
    duration = float((proj.get("media") or {}).get("duration") or 0.0)
    n_local = int(duration) + 1
    out: Dict[str, Any] = {"duration": duration, "width": width, "series": {}}
    if not sig:
        return out
    fused = score.build(sig, float(proj.get("offset_sec") or 0.0), n_local, proj["weights"])
    out["series"]["score"] = score.downsample_for_plot(
        align.smooth(fused["score"], max(2, int(proj["detect"].get("smooth", 8)))).astype(np.float32), width
    )
    for name, arr in fused["z"].items():
        out["series"][name] = score.downsample_for_plot(arr, width)
    out["threshold"] = proj["detect"].get("threshold")
    return out


# --------------------------------------------------------------------------- 4. 익스포트


def _selected(proj: Dict[str, Any]) -> List[Dict[str, Any]]:
    segs = [s for s in (proj.get("segments") or []) if s.get("selected")]
    return segs or list(proj.get("segments") or [])


def premiere_source(proj: Dict[str, Any], progress: Progress = _noop) -> str:
    """프리미어는 mkv 를 못 읽으므로 필요하면 mp4 로 리먹스한 경로를 준다."""
    src = proj["video_path"]
    if os.path.splitext(src)[1].lower() not in (".mkv", ".flv", ".ts", ".webm"):
        return src
    out = os.path.join(store.project_dir(proj["id"]), "exports",
                       os.path.splitext(os.path.basename(src))[0] + ".mp4")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    progress(0.05, "프리미어용 mp4 리먹스 중 (재인코딩 없음)…")
    ffmpeg_tools.remux_to_mp4(src, out)
    progress(0.9, "리먹스 완료")
    return out


def export_xml(pid: str, mode: str = "markers", remux: bool = True, progress: Progress = _noop) -> Dict[str, Any]:
    proj = store.load(pid)
    segs = _selected(proj)
    if not segs:
        raise RuntimeError("내보낼 후보가 없습니다.")
    path = premiere_source(proj, progress) if remux else proj["video_path"]
    media = dict(proj["media"])
    xml = export_xml_build(proj, media, segs, mode, path)
    out_dir = os.path.join(store.project_dir(pid), "exports")
    os.makedirs(out_dir, exist_ok=True)
    xml_path = os.path.join(out_dir, "%s_%s.xml" % (_slug(proj["name"]), mode))
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)
    csv_path = export_xml_csv(out_dir, proj, segs)
    txt_path = export_xml_txt(out_dir, proj, segs)
    progress(1.0, "XML 저장 완료")
    return {"xml": xml_path, "csv": csv_path, "txt": txt_path, "source": path, "segments": len(segs)}


def export_xml_build(proj, media, segs, mode, path):
    return xmlout.build_xml(
        path, media, segs, mode=mode,
        sequence_name="%s_%s" % (proj["name"], mode),
        audio_tracks=list(range(1, min(len(media.get("audio_streams") or [1]), 4) + 1)),
    )


def export_xml_csv(out_dir, proj, segs):
    return xmlout.write_csv(os.path.join(out_dir, "%s_후보목록.csv" % _slug(proj["name"])), segs, proj["media"])


def export_xml_txt(out_dir, proj, segs):
    return xmlout.write_txt(
        os.path.join(out_dir, "%s_후보목록.txt" % _slug(proj["name"])), segs, proj["media"], proj["name"]
    )


def export_previews(pid: str, seg_ids: Optional[List[int]] = None, progress: Progress = _noop) -> Dict[str, Any]:
    proj = store.load(pid)
    segs = _selected(proj)
    if seg_ids is not None:
        segs = [s for s in (proj.get("segments") or []) if s["id"] in seg_ids]
    out_dir = os.path.join(store.project_dir(pid), "clips")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for i, s in enumerate(segs):
        name = export_clips.safe_name(s) + ".mp4"
        out = os.path.join(out_dir, name)
        base = i / max(len(segs), 1)
        span = 1.0 / max(len(segs), 1)
        if seg_ids is not None or not (os.path.exists(out) and os.path.getsize(out) > 4096):
            export_clips.preview(
                proj["video_path"], s["start"], s["end"], out,
                proj.get("mix_stream"), int(proj["export"].get("preview_height", 540)),
                lambda f, m, b=base, sp=span: progress(b + sp * f, "[%d/%d] %s" % (i + 1, len(segs), m)),
            )
        s["preview"] = name
        made.append(name)
    store.save(proj)
    progress(1.0, "미리보기 %d개 생성" % len(made))
    return {"clips": made, "dir": out_dir}


def export_shorts(pid: str, seg_ids: Optional[List[int]] = None, progress: Progress = _noop) -> Dict[str, Any]:
    proj = store.load(pid)
    segs = _selected(proj)
    if seg_ids is not None:
        segs = [s for s in (proj.get("segments") or []) if s["id"] in seg_ids]
    out_dir = os.path.join(store.project_dir(pid), "shorts")
    os.makedirs(out_dir, exist_ok=True)
    ex = proj["export"]
    max_len = float(ex.get("shorts_max_len", 59))
    made = []
    for i, s in enumerate(segs):
        start, end = s["start"], s["end"]
        if end - start > max_len:  # 피크를 중심으로 자른다
            pk = s.get("peak", (start + end) / 2)
            start = max(0.0, pk - max_len * 0.6)
            end = start + max_len
        out = os.path.join(out_dir, export_clips.safe_name(s) + "_shorts.mp4")
        base = i / max(len(segs), 1)
        span = 1.0 / max(len(segs), 1)
        export_clips.shorts(
            proj["video_path"], start, end, out, proj.get("mix_stream"),
            ex.get("shorts_layout", "blur"), float(ex.get("shorts_focus_x", 0.5)),
            None,
            lambda f, m, b=base, sp=span: progress(b + sp * f, "[%d/%d] %s" % (i + 1, len(segs), m)),
        )
        s["shorts"] = os.path.basename(out)
        made.append(os.path.basename(out))
    store.save(proj)
    progress(1.0, "숏츠 %d개 생성" % len(made))
    return {"clips": made, "dir": out_dir}


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", name).strip("_")[:40] or "livehl"
