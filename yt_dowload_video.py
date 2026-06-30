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

# Global rate limiter cho subtitle requests
_subtitle_lock = threading.Lock()
_subtitle_last_request_time = 0.0
_SUBTITLE_MIN_INTERVAL = 2.0

import requests
import yt_dlp

from anti_ban import AntiBanManager, ExponentialBackoff, FailedURLLogger

YDL_OPTS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
    "rm_cachedir": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android"]
        }
    }
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


def get_cookie_browser_name(cookie_browser: str | bool) -> str | None:
    if isinstance(cookie_browser, bool):
        return "chrome" if cookie_browser else None
    if not cookie_browser:
        return None
    val = str(cookie_browser).strip().lower()
    if val in ("không dùng", "none", "false", ""):
        return None
    return val



def clean_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name)


def get_existing_titles(folder: str) -> tuple[set[str], int]:
    """
    Quét thư mục lưu trữ:
    - Trả về set các tiêu đề video (dưới dạng chữ thường, đã làm sạch để so sánh).
    - Trả về số thứ tự (index) lớn nhất hiện tại từ các file có dạng '### - Title.ext'.
    Quét đệ quy để hỗ trợ các thư mục con (ví dụ: locale).
    """
    existing_titles = set()
    max_idx = 0
    if not folder or not os.path.exists(folder):
        return existing_titles, max_idx

    # Pattern khớp với '### - Title.ext' hoặc '###_Title.ext'
    pattern = re.compile(r"^(\d+)\s*[-_]\s*(.*?)\.[a-zA-Z0-9]+$")
    try:
        for root, dirs, files in os.walk(folder):
            for name in files:
                match = pattern.match(name)
                if match:
                    idx_str, title = match.groups()
                    try:
                        idx = int(idx_str)
                        if idx > max_idx:
                            max_idx = idx
                    except ValueError:
                        pass
                    existing_titles.add(title.strip().lower())
                else:
                    base, _ = os.path.splitext(name)
                    existing_titles.add(base.strip().lower())
    except Exception:
        pass
    return existing_titles, max_idx


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


def extract_videos_from_url(url: str, cookie_browser: str | bool = "Không dùng") -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """
    Lấy metadata + danh sách [(video_id, cleaned_title), ...].
    Raises yt_dlp.utils.DownloadError hoặc ValueError nếu không có video.
    """
    ydl_opts = YDL_OPTS.copy()
    # Cookie: ưu tiên cookies.txt trước (static, kiểm soát được)
    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"
    browser_name = get_cookie_browser_name(cookie_browser)
    if browser_name:
        ydl_opts["cookiesfrombrowser"] = (browser_name, )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "cookiesfrombrowser" in ydl_opts and ("cookie" in err_msg.lower() or "decryption" in err_msg.lower() or "dpapi" in err_msg.lower()):
            print(f"[Warning] Lỗi lấy cookie từ trình duyệt {browser_name} (có thể trình duyệt đang mở hoặc bị mã hóa). Đang thử tải lại với cookies.txt...")
            ydl_opts.pop("cookiesfrombrowser", None)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        else:
            raise

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


def list_videos_from_url(url: str, cookie_browser: str | bool = "Không dùng") -> list[tuple[str, str]]:
    """[(video_id, cleaned_title), ...] — chỉ danh sách video."""
    _, videos = extract_videos_from_url(url, cookie_browser=cookie_browser)
    return videos


def summarize_url_videos(url: str, cookie_browser: str | bool = "Không dùng") -> tuple[int, str]:
    """
    Quét URL (kênh / tab / playlist / video đơn) với extract_flat.
    Trả về (số mục video, nhãn nguồn: kênh / playlist / tên).
    """
    info, videos = extract_videos_from_url(url, cookie_browser=cookie_browser)
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
    cookie_browser: str | bool = "Không dùng",
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
        "extractor_args": {
            "youtube": {
                "player_client": ["android"]
            }
        }
    }
    # Cookie: ưu tiên cookies.txt trước (static, kiểm soát được)
    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"
    browser_name = get_cookie_browser_name(cookie_browser)
    if browser_name:
        ydl_opts["cookiesfrombrowser"] = (browser_name, )
    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            err_msg = str(e)
            if "cookiesfrombrowser" in ydl_opts and ("cookie" in err_msg.lower() or "decryption" in err_msg.lower() or "dpapi" in err_msg.lower()):
                ydl_opts.pop("cookiesfrombrowser", None)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            else:
                raise
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
    cookie_browser: str | bool = "Không dùng",
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
            cookie_browser=cookie_browser,
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
    cookie_browser: str | bool = "Không dùng",
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

    info, videos = extract_videos_from_url(url, cookie_browser=cookie_browser)
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
                video_id, title, i, target_folder, locales, cancelled=is_cancelled, max_retries=thumb_max_retries, cookie_browser=cookie_browser
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
        # Không có ffprobe → chỉ kiểm tra file tồn tại và kích thước tối thiểu
        return os.path.isfile(path) and os.path.getsize(path) > 1024
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


def move_subtitle_files(video_folder: str, sub_folder: str, index: int, title: str) -> None:
    if not sub_folder or video_folder == sub_folder:
        return
    prefix = f"{index:03d} - {clean_filename(title)}"
    os.makedirs(sub_folder, exist_ok=True)
    try:
        for name in os.listdir(video_folder):
            if name.startswith(prefix) and (name.endswith(".vtt") or name.endswith(".srt") or name.endswith(".ass") or name.endswith(".sbv")):
                src = os.path.join(video_folder, name)
                dst = os.path.join(sub_folder, name)
                if os.path.exists(src):
                    shutil.move(src, dst)
    except Exception:
        pass


def move_audio_files(video_folder: str, audio_folder: str, index: int, title: str) -> None:
    if not audio_folder or video_folder == audio_folder:
        return
    prefix = f"{index:03d} - {clean_filename(title)}"
    os.makedirs(audio_folder, exist_ok=True)
    try:
        for name in os.listdir(video_folder):
            if name.startswith(prefix) and name.endswith(".mp3"):
                src = os.path.join(video_folder, name)
                dst = os.path.join(audio_folder, name)
                if os.path.exists(src):
                    shutil.move(src, dst)
    except Exception:
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
    cookie_browser: str | bool = "Không dùng",
    download_video: bool = True,
    download_sub: bool = False,
    sub_locales: list[str] | None = None,
    sub_folder: str | None = None,
    download_audio: bool = False,
    audio_folder: str | None = None,
) -> bool:
    if cancelled():
        return False

    url = f"https://www.youtube.com/watch?v={video_id}"

    clean_t = clean_filename(title)
    final_path = expected_video_path(output_folder, index, title)

    backoff = ExponentialBackoff(max_retries=retries) if retries > 0 else None

    while True:
        try:
            target_out_folder = output_folder
            if not download_video:
                if download_audio and audio_folder:
                    target_out_folder = audio_folder
                elif download_sub and sub_folder:
                    target_out_folder = sub_folder

            if not download_video and download_audio:
                fmt = "bestaudio/best"
            else:
                fmt = quality_to_format(quality)

            ydl_opts: dict[str, Any] = {
                "quiet": True,
                "format": fmt,
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(target_out_folder, f"{index:03d} - {clean_t}.%(ext)s"),
                "postprocessor_args": {
                    "Merger+ffmpeg_o": ["-movflags", "+faststart"],
                },
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"]
                    }
                }
            }
            if not download_video and not download_audio:
                ydl_opts["skip_download"] = True

            if download_video and download_audio:
                ydl_opts["keepvideo"] = True

            if download_audio:
                ydl_opts["postprocessors"] = ydl_opts.get("postprocessors", []) + [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ]

            # Tải phụ đề qua yt-dlp thay vì requests thủ công
            if download_sub:
                sub_langs = ",".join(sub_locales) if sub_locales and "all" not in sub_locales else "vi,en"
                ydl_opts["writesubtitles"] = True
                ydl_opts["writeautomaticsub"] = True
                ydl_opts["subtitleslangs"] = [sub_langs]
                ydl_opts["subtitlesformat"] = "srt"

            # Cookie: ưu tiên cookies.txt trước (static, kiểm soát được)
            if os.path.exists("cookies.txt"):
                ydl_opts["cookiefile"] = "cookies.txt"
            browser_name = get_cookie_browser_name(cookie_browser)
            if browser_name:
                ydl_opts["cookiesfrombrowser"] = (browser_name, )
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                ydl_opts["ffmpeg_location"] = ffmpeg_bin

            if anti_ban:
                ydl_opts = anti_ban.create_ydl_opts(ydl_opts)
                if ydl_opts.get("proxy"):
                    on_log(f"[{index}] Proxy: {ydl_opts['proxy']}")

            def progress_hook(d: dict[str, Any]) -> None:
                if cancelled():
                    raise yt_dlp.utils.DownloadError("Cancelled by user")

            ydl_opts["progress_hooks"] = [progress_hook]

            info_dict = None
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=not (download_sub and not download_video and not download_audio))
            except yt_dlp.utils.DownloadError as e:
                err_msg = str(e)
                if err_msg == "Cancelled by user":
                    return False
                if "cookiesfrombrowser" in ydl_opts and ("cookie" in err_msg.lower() or "decryption" in err_msg.lower() or "dpapi" in err_msg.lower()):
                    on_log(f"[{index}] Lỗi lấy cookie từ trình duyệt {browser_name}. Đang thử lại với cookies.txt...")
                    ydl_opts.pop("cookiesfrombrowser", None)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info_dict = ydl.extract_info(url, download=not (download_sub and not download_video and not download_audio))
                else:
                    raise

            if download_video:
                if not verify_merged_mp4(final_path):
                    cleanup_video_artifacts(output_folder, index, title)
                    on_log(
                        f"[{index}] File MP4 không hợp lệ (thiếu video/audio hoặc ghép lỗi): {title}"
                    )
                    return False

            # Di chuyển phụ đề sang sub_folder nếu cần
            if download_sub and sub_folder and output_folder != sub_folder:
                move_subtitle_files(output_folder, sub_folder, index, title)

            if download_audio and download_video and audio_folder and output_folder != audio_folder:
                move_audio_files(output_folder, audio_folder, index, title)

            if anti_ban:
                anti_ban.increment_download_count()
                if anti_ban.should_change_vpn():
                    anti_ban.change_vpn(on_log)
                    anti_ban.reset_vpn_counter()

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
    download_sub: bool = False,
    sub_folder: str | None = None,
    sub_locales: list[str] | None = None,
    thumb_locales: list[str] | None = None,
    thumb_max_retries: int = 5,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    anti_ban_config: dict[str, Any] | None = None,
    max_workers: int = 1,
    cookie_browser: str | bool = "Không dùng",
    download_audio: bool = False,
    audio_folder: str | None = None,
) -> tuple[int, int]:
    is_cancelled = cancelled or (lambda: False)
    log = on_log or (lambda _m: None)
    prog = on_progress or (lambda _c, _t: None)

    anti_ban: AntiBanManager | None = None
    failed_logger = FailedURLLogger()

    if anti_ban_config:
        anti_ban = AntiBanManager(
            min_delay=anti_ban_config.get("min_delay", 8.0),
            max_delay=anti_ban_config.get("max_delay", 25.0),
            rate_limit=anti_ban_config.get("rate_limit", 2_000_000),
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
        info, videos = extract_videos_from_url(url, cookie_browser=cookie_browser)
        sub = _folder_name_from_info(info)

    v_target = video_folder if video_folder else ""
    t_target = thumb_folder if thumb_folder else ""
    s_target = sub_folder if sub_folder else ""
    a_target = audio_folder if audio_folder else ""

    if download_video and v_target:
        os.makedirs(v_target, exist_ok=True)
    if download_thumb and t_target:
        os.makedirs(t_target, exist_ok=True)
    if download_sub and s_target:
        os.makedirs(s_target, exist_ok=True)
    elif download_sub and v_target:
        os.makedirs(v_target, exist_ok=True)
    if download_audio and a_target:
        os.makedirs(a_target, exist_ok=True)
    elif download_audio and v_target:
        os.makedirs(v_target, exist_ok=True)

    if download_thumb and t_target and thumb_locale_codes:
        log(f"Thumbnail theo ngôn ngữ (thư mục con): {', '.join(thumb_locale_codes)}")

    master_folder = v_target if (download_video and v_target) else (a_target if (download_audio and a_target) else (s_target if (download_sub and s_target) else (t_target if (download_thumb and t_target) else "")))
    existing_titles, max_idx = get_existing_titles(master_folder)
    if existing_titles:
        log(f"[Resume] Phát hiện {len(existing_titles)} file đã tải trong thư mục. Index lớn nhất hiện tại: {max_idx}")

    next_idx = max_idx + 1
    lock_idx = threading.Lock()

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

        # Kiểm tra trùng lặp
        clean_title = clean_filename(title).strip().lower()
        if clean_title in existing_titles:
            log(f"[{i}] Bỏ qua (Đã tồn tại): {title}")
            with lock_ok:
                ok += 1
                completed += 1
                current_completed = completed
            prog(current_completed, total)
            return True

        # Cấp số thứ tự (index) thread-safe tiếp theo
        with lock_idx:
            nonlocal next_idx
            current_idx = next_idx
            next_idx += 1

        item_ok = True

        if (download_video and v_target) or (download_audio and (a_target or v_target)) or (download_sub and (s_target or v_target)):
            action_desc = []
            if download_video:
                action_desc.append("video")
            if download_audio:
                action_desc.append("audio")
            if download_sub:
                action_desc.append("phụ đề")
            action_str = " + ".join(action_desc)
            log(f"[{i}] (File #{current_idx:03d}) Đang tải {action_str}: {title}...")
            v_success = download_single_video(
                video_id, title, current_idx, v_target or a_target or s_target, quality,
                on_log=log, cancelled=is_cancelled,
                anti_ban=anti_ban,
                retries=anti_ban_config.get("max_retries", 3) if anti_ban_config else 0,
                cookie_browser=cookie_browser,
                download_video=download_video,
                download_sub=download_sub,
                sub_locales=sub_locales,
                sub_folder=s_target,
                download_audio=download_audio,
                audio_folder=a_target,
            )
            if not v_success:
                item_ok = False
                log(f"[{i}] (File #{current_idx:03d}) Tải {action_str} thất bại: {title}")
                if download_video and anti_ban_config:
                    failed_logger.log_failed_url(
                        f"https://www.youtube.com/watch?v={video_id}",
                        "video_download_failed",
                        str(title),
                    )

        if download_thumb and t_target and not is_cancelled():
            log(f"[{i}] (File #{current_idx:03d}) Đang tải thumbnail: {title}...")
            if api_video_dict and video_id in api_video_dict:
                t_success = download_thumbnail_from_api(
                    api_video_dict[video_id],
                    current_idx,
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
                        current_idx,
                        t_target,
                        thumb_locale_codes,
                        cancelled=is_cancelled,
                        max_retries=thumb_max_retries,
                        cookie_browser=cookie_browser,
                    )
                else:
                    t_success = download_thumbnail_file(video_id, title, current_idx, t_target, max_retries=thumb_max_retries)
                    
            if not t_success:
                item_ok = False
                log(f"[{i}] (File #{current_idx:03d}) Tải thumbnail thất bại: {title}")
                if anti_ban_config:
                    failed_logger.log_failed_url(
                        f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail_download_failed",
                        str(title),
                    )

        with lock_ok:
            if item_ok:
                ok += 1
                log(f"[{i}] Hoàn thành (File #{current_idx:03d}): {title}")
            else:
                fail += 1

        with lock_ok:
            completed += 1
            current_completed = completed
        prog(current_completed, total)
        return item_ok

    if max_workers > 1:
        log(f"[Parallel] Sử dụng {max_workers} threads để tải...")
        submit_delay = anti_ban.get_random_delay() if anti_ban else 3.0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, (vid, title) in enumerate(videos, start=1):
                if is_cancelled():
                    break
                future = executor.submit(download_single_item, i, vid, title)
                futures[future] = (i, vid, title)
                # Chỉ delay giữa mỗi batch max_workers task, không delay mỗi task
                if i % max_workers == 0 and i < len(videos):
                    time.sleep(random.uniform(1.0, submit_delay))
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
