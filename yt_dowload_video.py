"""Lõi tải thumbnail YouTube (kênh / playlist / video đơn) — dùng chung CLI và GUI."""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
import yt_dlp

from anti_ban import AntiBanManager, ExponentialBackoff, FailedURLLogger

YDL_OPTS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
    "cookiesfrombrowser": ("chrome", ),
}

# Tiêu đề thumbnail theo vùng: map mã thư mục → giá trị Accept-Language (ISO giống trình duyệt).
THUMB_LOCALE_ACCEPT_LANGUAGE: dict[str, str] = {
    "en": "en-US,en;q=0.9",
    "ko": "ko-KR,ko;q=0.9",
    "ja": "ja-JP,ja;q=0.9",
}


def normalize_thumb_locales(locales: list[str] | None) -> list[str]:
    """Chỉ giữ các mã được hỗ trợ, giữ thứ tự, bỏ trùng."""
    if not locales:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for code in locales:
        c = str(code).strip().lower()
        if c not in THUMB_LOCALE_ACCEPT_LANGUAGE or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


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


def fetch_thumbnail_jpeg_bytes(video_id: str, max_retries: int = 5) -> bytes | None:
    """Tải bytes JPEG thumbnail (maxres fallback hq) với tự động thử lại."""
    thumb_urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for attempt in range(max_retries):
        for thumb_url in thumb_urls:
            try:
                r = requests.get(thumb_url, timeout=10)
                if r.status_code == 200 and r.content:
                    return r.content
            except requests.RequestException:
                continue
        if attempt < max_retries - 1:
            time.sleep(1 * (attempt + 1))
    return None


def save_thumbnail_jpeg(output_folder: str, index: int, title: str, data: bytes) -> bool:
    """Ghi {index:03d} - {title}.jpg vào output_folder."""
    try:
        os.makedirs(output_folder, exist_ok=True)
        file_name = f"{index:03d} - {clean_filename(title)}.jpg"
        path = os.path.join(output_folder, file_name)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except OSError:
        return False


def fetch_video_title_for_locale(
    video_id: str,
    locale_code: str,
    *,
    fallback_title: str,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Trích tiêu đề watch page với Accept-Language tương ứng locale."""
    is_cancelled = cancelled or (lambda: False)
    if is_cancelled():
        return fallback_title
    accept = THUMB_LOCALE_ACCEPT_LANGUAGE.get(locale_code)
    if not accept:
        accept = f"{locale_code}-{locale_code.upper()},{locale_code};q=0.9"
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
        "http_headers": {"Accept-Language": accept},
        "cookiesfrombrowser": ("chrome", ),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if isinstance(info, dict):
            t = info.get("title")
            if t is not None:
                s = str(t).strip()
                if s:
                    return s
    except Exception:
        pass
    return fallback_title


def download_thumbnail_file(
    video_id: str,
    title: str,
    index: int,
    output_folder: str,
    max_retries: int = 5,
) -> bool:
    """Tải một thumbnail; tên file {index:03d} - {title}.jpg. Trả về True nếu thành công."""
    data = fetch_thumbnail_jpeg_bytes(video_id, max_retries=max_retries)
    if not data:
        return False
    return save_thumbnail_jpeg(output_folder, index, title, data)


def download_thumbnail_multi_locale(
    video_id: str,
    fallback_title: str,
    index: int,
    base_folder: str,
    locales: list[str],
    *,
    cancelled: Callable[[], bool] | None = None,
    max_retries: int = 5,
) -> bool:
    """
    Tải thumbnail một lần, ghi vào base_folder/<locale>/ với tiêu đề theo từng ngôn ngữ.
    locales đã được normalize (vd. en, ko).
    """
    is_cancelled = cancelled or (lambda: False)
    data = fetch_thumbnail_jpeg_bytes(video_id, max_retries=max_retries)
    if not data:
        return False
    clean_fallback = clean_filename(fallback_title)
    for loc in locales:
        if is_cancelled():
            return False
        title_loc = fetch_video_title_for_locale(
            video_id,
            loc,
            fallback_title=clean_fallback,
            cancelled=cancelled,
        )
        locale_folder = os.path.join(base_folder, loc)
        if not save_thumbnail_jpeg(locale_folder, index, title_loc, data):
            return False
    return True


def download_thumbnails_batch(
    url: str,
    output_folder: str,
    *,
    thumb_locales: list[str] | None = None,
    thumb_max_retries: int = 5,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """
    Tải toàn bộ thumbnail cho URL.
    on_progress(current, total), on_log(message).
    cancelled(): nếu True thì dừng sớm.
    Trả về (số thành công, số thất bại trong phần đã chạy).

    thumb_locales: ví dụ ``[\"en\", \"ko\"]`` → ảnh lưu vào ``.../<kênh>/en/``, ``.../ko/``
    với tên file theo tiêu đề tương ứng. Rỗng / None → một thư mục như trước.
    thumb_max_retries: số lần thử lại khi tải thumbnail thất bại (mặc định 5).
    """
    is_cancelled = cancelled or (lambda: False)
    log = on_log or (lambda _m: None)
    prog = on_progress or (lambda _c, _t: None)

    info, videos = extract_videos_from_url(url)
    sub = _folder_name_from_info(info)
    target_folder = os.path.join(output_folder, sub)
    os.makedirs(target_folder, exist_ok=True)
    log(f"Output folder: {target_folder}")

    locales = normalize_thumb_locales(thumb_locales)
    if locales:
        log(f"Thumbnail theo ngôn ngữ (thư mục con): {', '.join(locales)}")

    total = len(videos)
    ok = 0
    fail = 0

    for i, (video_id, title) in enumerate(videos, start=1):
        if is_cancelled():
            log("Cancelled.")
            break
        prog(i, total)
        if locales:
            if download_thumbnail_multi_locale(
                video_id, title, i, target_folder, locales, cancelled=is_cancelled, max_retries=thumb_max_retries
            ):
                ok += 1
                log(f"[{i}] Downloaded ({', '.join(locales)}): {title}")
            else:
                fail += 1
                log(f"[{i}] Failed: {title}")
        elif download_thumbnail_file(video_id, title, i, target_folder, max_retries=thumb_max_retries):
            ok += 1
            log(f"[{i}] Downloaded: {title}")
        else:
            fail += 1
            log(f"[{i}] Failed: {title}")

    return ok, fail


def quality_to_format(quality: str) -> str:
    """Map chất lượng UI → chuỗi format yt-dlp (ưu tiên H.264 + AAC, ghép được)."""
    height = {
        "360p": 360,
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
    }.get(quality)
    if height is not None:
        h = f"[height<={height}]"
        return (
            f"bestvideo{h}[vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo{h}+bestaudio/"
            f"best{h}"
        )
    return (
        "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
        "bestvideo+bestaudio/best"
    )


def expected_video_path(output_folder: str, index: int, title: str) -> str:
    clean_t = clean_filename(title)
    return os.path.join(output_folder, f"{index:03d} - {clean_t}.mp4")


def verify_merged_mp4(path: str) -> bool:
    """File MP4 hợp lệ phải có cả track video và audio."""
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return False
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return False
        stream_types = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        return "video" in stream_types and "audio" in stream_types
    except (OSError, subprocess.SubprocessError):
        return False


def cleanup_video_artifacts(output_folder: str, index: int, title: str) -> None:
    """Xóa file tạm khi ghép video/audio thất bại (.fNNN.*, .temp.mp4, …)."""
    prefix = f"{index:03d} - {clean_filename(title)}"
    try:
        for name in os.listdir(output_folder):
            if name.startswith(prefix) and name != f"{prefix}.mp4":
                try:
                    os.remove(os.path.join(output_folder, name))
                except OSError:
                    pass
    except OSError:
        pass


def download_single_video(
    video_id: str,
    title: str,
    index: int,
    output_folder: str,
    quality: str,
    *,
    on_log: Callable[[str], None],
    cancelled: Callable[[], bool],
    anti_ban: AntiBanManager | None = None,
    retries: int = 0,
) -> bool:
    if cancelled():
        return False

    url = f"https://www.youtube.com/watch?v={video_id}"
    clean_t = clean_filename(title)
    final_path = expected_video_path(output_folder, index, title)

    backoff = ExponentialBackoff(max_retries=retries) if retries > 0 else None

    while True:
        try:
            ydl_opts: dict[str, Any] = {
                "quiet": True,
                "format": quality_to_format(quality),
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(output_folder, f"{index:03d} - {clean_t}.%(ext)s"),
                "postprocessor_args": {
                    "Merger+ffmpeg_o": ["-movflags", "+faststart"],
                },
                "cookiesfrombrowser": ("chrome", ),
            }
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                ydl_opts["ffmpeg_location"] = ffmpeg_bin

            if anti_ban:
                ydl_opts = anti_ban.create_ydl_opts(ydl_opts)

            def progress_hook(d: dict[str, Any]) -> None:
                if cancelled():
                    pass

            ydl_opts["progress_hooks"] = [progress_hook]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            if not verify_merged_mp4(final_path):
                cleanup_video_artifacts(output_folder, index, title)
                on_log(
                    f"[{index}] File MP4 không hợp lệ (thiếu video/audio hoặc ghép lỗi): {title}"
                )
                return False

            if anti_ban:
                anti_ban.increment_download_count()
                if anti_ban.should_change_vpn():
                    anti_ban.change_vpn(on_log)

            return True

        except yt_dlp.utils.DownloadError as e:
            error_str = str(e)
            if "429" in error_str and backoff and backoff.should_retry():
                delay = backoff.get_delay()
                on_log(f"[{index}] Rate limited, chờ {delay:.1f}s...")
                time.sleep(delay)
                backoff.increment()
                continue
            on_log(f"[{index}] Lỗi tải video {title}: {e}")
            return False
        except Exception as e:
            on_log(f"[{index}] Lỗi tải video {title}: {e}")
            return False


def download_thumbnail_from_api(
    video_data: dict,
    index: int,
    base_folder: str,
    locales: list[str],
    *,
    cancelled: Callable[[], bool] | None = None,
    max_retries: int = 5,
) -> bool:
    is_cancelled = cancelled or (lambda: False)
    thumbnail_url = video_data.get('thumbnail_url')
    if not thumbnail_url:
        return False
        
    data = None
    for attempt in range(max_retries):
        try:
            r = requests.get(thumbnail_url, timeout=10)
            if r.status_code == 200 and r.content:
                data = r.content
                break
        except requests.RequestException:
            pass
        if attempt < max_retries - 1:
            time.sleep(1 * (attempt + 1))
            
    if not data:
        return False
        
    default_title = video_data['default_title']
    
    if not locales:
        return save_thumbnail_jpeg(base_folder, index, default_title, data)
        
    for loc in locales:
        if is_cancelled():
            return False
        title_loc = video_data['localized_titles'].get(loc, default_title)
        locale_folder = os.path.join(base_folder, loc)
        if not save_thumbnail_jpeg(locale_folder, index, title_loc, data):
            return False
    return True


def download_job_batch(
    url: str,
    video_folder: str,
    thumb_folder: str,
    *,
    download_video: bool,
    download_thumb: bool,
    quality: str,
    thumb_locales: list[str] | None = None,
    thumb_max_retries: int = 5,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    anti_ban_config: dict[str, Any] | None = None,
    max_workers: int = 1,
) -> tuple[int, int]:
    is_cancelled = cancelled or (lambda: False)
    log = on_log or (lambda _m: None)
    prog = on_progress or (lambda _c, _t: None)

    anti_ban: AntiBanManager | None = None
    failed_logger = FailedURLLogger()

    if anti_ban_config:
        anti_ban = AntiBanManager(
            min_delay=anti_ban_config.get("min_delay", 5.5),
            max_delay=anti_ban_config.get("max_delay", 15.0),
            rate_limit=anti_ban_config.get("rate_limit", 5_000_000),
            proxy_list=anti_ban_config.get("proxy_list"),
            vpn_enabled=anti_ban_config.get("vpn_enabled", False),
            vpn_change_interval=anti_ban_config.get("vpn_change_interval", 30),
            use_fake_ua=anti_ban_config.get("use_fake_ua", True),
        )
        log("[AntiBan] Đã khởi tạo Anti-Ban Manager")

    if download_video and shutil.which("ffmpeg") is None:
        raise ValueError("Không tìm thấy ffmpeg. Vui lòng cài đặt ffmpeg và thêm vào PATH để có thể ghép audio và video.")

    thumb_locale_codes = normalize_thumb_locales(thumb_locales)
    
    api_video_dict = None
    try:
        import youtube_api_utils
        log("[Google API] Bắt đầu lấy danh sách video bằng API...")
        api_videos, sub = youtube_api_utils.fetch_videos_from_api(url, thumb_locale_codes)
        videos = []
        api_video_dict = {}
        for v in api_videos:
            videos.append((v['id'], v['default_title']))
            api_video_dict[v['id']] = v
    except Exception as e:
        log(f"[Google API] Lỗi: {e}. Đang dùng yt-dlp dự phòng...")
        info, videos = extract_videos_from_url(url)
        sub = _folder_name_from_info(info)

    v_target = video_folder if video_folder else ""
    t_target = thumb_folder if thumb_folder else ""

    if download_video and v_target:
        os.makedirs(v_target, exist_ok=True)
    if download_thumb and t_target:
        os.makedirs(t_target, exist_ok=True)

    if download_thumb and t_target and thumb_locale_codes:
        log(f"Thumbnail theo ngôn ngữ (thư mục con): {', '.join(thumb_locale_codes)}")

    total = len(videos)
    ok = 0
    fail = 0
    completed = 0
    lock_ok = threading.Lock()
    lock_fail = threading.Lock()

    def download_single_item(i: int, video_id: str, title: str) -> bool:
        nonlocal ok, fail, completed
        if is_cancelled():
            return False

        item_ok = True

        if download_video and v_target:
            log(f"[{i}] Đang tải video: {title}...")
            v_success = download_single_video(
                video_id, title, i, v_target, quality,
                on_log=log, cancelled=is_cancelled,
                anti_ban=anti_ban,
                retries=anti_ban_config.get("max_retries", 3) if anti_ban_config else 0,
            )
            if not v_success:
                item_ok = False
                log(f"[{i}] Tải video thất bại: {title}")
                if anti_ban_config:
                    failed_logger.log_failed_url(
                        f"https://www.youtube.com/watch?v={video_id}",
                        "video_download_failed",
                        str(title),
                    )

        if download_thumb and t_target and not is_cancelled():
            log(f"[{i}] Đang tải thumbnail: {title}...")
            if api_video_dict and video_id in api_video_dict:
                t_success = download_thumbnail_from_api(
                    api_video_dict[video_id],
                    i,
                    t_target,
                    thumb_locale_codes,
                    cancelled=is_cancelled,
                    max_retries=thumb_max_retries,
                )
            else:
                if thumb_locale_codes:
                    t_success = download_thumbnail_multi_locale(
                        video_id,
                        title,
                        i,
                        t_target,
                        thumb_locale_codes,
                        cancelled=is_cancelled,
                        max_retries=thumb_max_retries,
                    )
                else:
                    t_success = download_thumbnail_file(video_id, title, i, t_target, max_retries=thumb_max_retries)
                    
            if not t_success:
                item_ok = False
                log(f"[{i}] Tải thumbnail thất bại: {title}")

        with lock_ok:
            if item_ok:
                ok += 1
                log(f"[{i}] Hoàn thành: {title}")
            else:
                fail += 1

        nonlocal completed
        completed += 1
        prog(completed, total)
        return item_ok

    if max_workers > 1:
        log(f"[Parallel] Sử dụng {max_workers} threads để tải...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_single_item, i, vid, title): (i, vid, title)
                for i, (vid, title) in enumerate(videos, start=1)
            }
            for future in as_completed(futures):
                if is_cancelled():
                    break
    else:
        for i, (video_id, title) in enumerate(videos, start=1):
            if is_cancelled():
                log("Đã hủy.")
                break

            if anti_ban and i > 1:
                delay = anti_ban.get_random_delay()
                log(f"[AntiBan] Chờ {delay:.1f}s trước video tiếp theo...")
                time.sleep(delay)

            download_single_item(i, video_id, title)

    return ok, fail
