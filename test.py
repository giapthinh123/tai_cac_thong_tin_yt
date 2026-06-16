import yt_dlp
import os
import re
import requests
import time

channel_url = "https://www.youtube.com/@MaestradelDestino/videos"
output_folder = "subtitles"
os.makedirs(output_folder, exist_ok=True)

# ✅ Cấu hình proxy
PROXY = "http://sp08-14498:MQXHO@103.179.172.29:14498"

def clean_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

# Lấy danh sách video
ydl_opts = {
    'quiet': True,
    'extract_flat': True,
    'cookiefile': 'cookies.txt',
    'proxy': PROXY,  # ✅ Proxy cho yt-dlp
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(channel_url, download=False)
    videos = list(info['entries'])

print(f"Found {len(videos)} videos")

lang_code = "es"

# ✅ Session requests với proxy
session = requests.Session()
session.proxies = {
    "http": PROXY,
    "https": PROXY,
}
# http://sp08v2-22692:QUNOO@103.67.199.60:22692
# http://sp08v2-22761:NMFST@103.67.199.60:22761
# http://sp08v1-17938:JQKWL@103.27.62.179:17938
# http://sp08-15381:QJIAO@103.179.172.132:15381
# http://sp08-16251:FACUQ@103.179.173.8:16251
# http://sp08-14498:MQXHO@103.179.172.29:14498
for index, video in enumerate(videos, start=1):
    video_id = video.get("id")
    title = clean_filename(video.get("title", "no_title"))
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        ydl_video_opts = {
            'quiet': True,
            'cookiefile': 'cookies.txt',
            'no_warnings': True,
            'ignore_no_formats_error': True,
            'skip_download': True,
            'format': None,
            'noplaylist': True,
            'proxy': PROXY,  # ✅ Proxy cho yt-dlp
        }
        with yt_dlp.YoutubeDL(ydl_video_opts) as ydl:
            data = ydl.extract_info(url, download=False)

        sub_url = None

        # Ưu tiên: phụ đề tự động
        auto_caps = data.get("automatic_captions", {})
        if lang_code in auto_caps:
            for fmt in auto_caps[lang_code]:
                if fmt.get("ext") == "srt":
                    sub_url = fmt["url"]
                    break

        # Dự phòng: phụ đề thủ công
        if not sub_url:
            subs = data.get("subtitles", {})
            if lang_code in subs:
                for fmt in subs[lang_code]:
                    if fmt.get("ext") == "srt":
                        sub_url = fmt["url"]
                        break

        if not sub_url:
            print(f"[{index}] No subtitles ({lang_code}): {title}")
            continue

        response = session.get(sub_url, timeout=30)  # ✅ Dùng session có proxy
        if response.status_code == 200:
            filename = os.path.join(output_folder, f"{index:03d} - {title}.srt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(response.text)
            print(f"[{index}] Downloaded: {title}")
        else:
            print(f"[{index}] Failed HTTP {response.status_code}: {title}")

    except Exception as e:
        print(f"[{index}] Failed: {title} — {e}")

    time.sleep(1)