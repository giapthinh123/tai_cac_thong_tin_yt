"""Lõi tải thumbnail YouTube (kênh / playlist / video đơn) — dùng chung CLI và GUI."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from typing import Any

import requests
import yt_dlp

YDL_OPTS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
}


def clean_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name)


def _normalize_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Trả về danh sách entry phẳng: mỗi phần tử có id (video_id) và title."""
    entries = info.get("entries")
    if entries:
        out: list[dict[str, Any]] = []
        for e in entries:
            if e is None:
                continue
            vid = e.get("id")
            if not vid:
                continue
            out.append(e)
        if out:
            return out

    # Một video đơn (flat / trang trực tiếp)
    itype = info.get("_type")
    vid = info.get("id")
    if vid and itype in ("video", "url"):
        return [{"id": vid, "title": info.get("title") or "no_title"}]

    return []


def _folder_name_from_info(info: dict[str, Any]) -> str:
    """Tên thư mục con: ưu tiên kênh / uploader / playlist / tiêu đề."""
    for key in ("channel", "uploader", "playlist_title", "title"):
        val = info.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            name = clean_filename(s)
            if name:
                return name[:200]

    entries = info.get("entries") or []
    for e in entries:
        if not isinstance(e, dict):
            continue
        for key in ("channel", "uploader"):
            val = e.get(key)
            if val is None:
                continue
            s = str(val).strip()
            if s:
                name = clean_filename(s)
                if name:
                    return name[:200]

    return "Unknown"


def extract_videos_from_url(url: str) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """
    Lấy metadata + danh sách [(video_id, cleaned_title), ...].
    Raises yt_dlp.utils.DownloadError hoặc ValueError nếu không có video.
    """
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise ValueError("Không đọc được metadata từ URL.")

    raw = _normalize_entries(info)
    if not raw:
        raise ValueError("Không tìm thấy video nào cho URL này.")

    videos = [
        (str(e["id"]), clean_filename(str(e.get("title") or "no_title")))
        for e in raw
    ]
    return info, videos


def list_videos_from_url(url: str) -> list[tuple[str, str]]:
    """[(video_id, cleaned_title), ...] — chỉ danh sách video."""
    _, videos = extract_videos_from_url(url)
    return videos


def summarize_url_videos(url: str) -> tuple[int, str]:
    """
    Quét URL (kênh / tab / playlist / video đơn) với extract_flat.
    Trả về (số mục video, nhãn nguồn: kênh / playlist / tên).
    """
    info, videos = extract_videos_from_url(url)
    label = _folder_name_from_info(info)
    return len(videos), label


def download_thumbnail_file(
    video_id: str,
    title: str,
    index: int,
    output_folder: str,
) -> bool:
    """Tải một thumbnail; tên file {index:03d} - {title}.jpg. Trả về True nếu thành công."""
    file_name = f"{index:03d} - {title}.jpg"
    thumb_urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for thumb_url in thumb_urls:
        try:
            r = requests.get(thumb_url, timeout=10)
            if r.status_code == 200 and r.content:
                path = os.path.join(output_folder, file_name)
                with open(path, "wb") as f:
                    f.write(r.content)
                return True
        except requests.RequestException:
            continue
    return False


def download_thumbnails_batch(
    url: str,
    output_folder: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """
    Tải toàn bộ thumbnail cho URL.
    on_progress(current, total), on_log(message).
    cancelled(): nếu True thì dừng sớm.
    Trả về (số thành công, số thất bại trong phần đã chạy).
    """
    is_cancelled = cancelled or (lambda: False)
    log = on_log or (lambda _m: None)
    prog = on_progress or (lambda _c, _t: None)

    info, videos = extract_videos_from_url(url)
    sub = _folder_name_from_info(info)
    target_folder = os.path.join(output_folder, sub)
    os.makedirs(target_folder, exist_ok=True)
    log(f"Output folder: {target_folder}")

    total = len(videos)
    ok = 0
    fail = 0

    for i, (video_id, title) in enumerate(videos, start=1):
        if is_cancelled():
            log("Cancelled.")
            break
        prog(i, total)
        if download_thumbnail_file(video_id, title, i, target_folder):
            ok += 1
            log(f"[{i}] Downloaded: {title}")
        else:
            fail += 1
            log(f"[{i}] Failed: {title}")

    return ok, fail


def quality_to_format(quality: str) -> str:
    """Map UI quality string to yt-dlp format string."""
    if quality == "360p":
        return "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]/best[ext=mp4][height<=360]/best"
    elif quality == "480p":
        return "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]/best"
    elif quality == "720p":
        return "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best"
    elif quality == "1080p":
        return "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best"
    elif quality == "Tốt nhất":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    else:
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


def download_single_video(
    video_id: str,
    title: str,
    index: int,
    output_folder: str,
    quality: str,
    *,
    on_log: Callable[[str], None],
    cancelled: Callable[[], bool],
) -> bool:
    if cancelled():
        return False

    url = f"https://www.youtube.com/watch?v={video_id}"
    clean_t = clean_filename(title)
    
    ydl_opts = {
        "quiet": True,
        "format": quality_to_format(quality),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(output_folder, f"{index:03d} - {clean_t}.%(ext)s"),
    }
    
    def progress_hook(d: dict[str, Any]) -> None:
        if cancelled():
            # yt-dlp doesn't have a direct cancel hook except raising an exception
            pass

    ydl_opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        on_log(f"[{index}] Lỗi tải video {title}: {e}")
        return False


def download_job_batch(
    url: str,
    video_folder: str,
    thumb_folder: str,
    *,
    download_video: bool,
    download_thumb: bool,
    quality: str,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    is_cancelled = cancelled or (lambda: False)
    log = on_log or (lambda _m: None)
    prog = on_progress or (lambda _c, _t: None)

    if download_video and shutil.which("ffmpeg") is None:
        raise ValueError("Không tìm thấy ffmpeg. Vui lòng cài đặt ffmpeg và thêm vào PATH để có thể ghép audio và video.")

    info, videos = extract_videos_from_url(url)
    sub = _folder_name_from_info(info)

    v_target = os.path.join(video_folder, sub) if video_folder else ""
    t_target = os.path.join(thumb_folder, sub) if thumb_folder else ""

    if download_video and v_target:
        os.makedirs(v_target, exist_ok=True)
    if download_thumb and t_target:
        os.makedirs(t_target, exist_ok=True)

    total = len(videos)
    ok = 0
    fail = 0

    for i, (video_id, title) in enumerate(videos, start=1):
        if is_cancelled():
            log("Đã hủy.")
            break
        
        prog(i, total)
        item_ok = True
        
        if download_video and v_target:
            log(f"[{i}] Đang tải video: {title}...")
            v_success = download_single_video(
                video_id, title, i, v_target, quality,
                on_log=log, cancelled=is_cancelled
            )
            if not v_success:
                item_ok = False
                log(f"[{i}] Tải video thất bại: {title}")

        if download_thumb and t_target:
            log(f"[{i}] Đang tải thumbnail: {title}...")
            t_success = download_thumbnail_file(video_id, title, i, t_target)
            if not t_success:
                item_ok = False
                log(f"[{i}] Tải thumbnail thất bại: {title}")
                
        if item_ok:
            ok += 1
            log(f"[{i}] Hoàn thành: {title}")
        else:
            fail += 1

    return ok, fail
