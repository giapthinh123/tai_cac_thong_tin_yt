"""Module Anti-Ban cho YouTube Downloader."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

try:
    import fake_useragent
    FAKE_USERAGENT_AVAILABLE = True
except ImportError:
    FAKE_USERAGENT_AVAILABLE = False


class AntiBanManager:
    """Quản lý các chiến lược Anti-Ban."""

    def __init__(
        self,
        min_delay: float = 5.5,
        max_delay: float = 15.0,
        rate_limit: int = 10_000_000,
        proxy_list: list[str] | None = None,
        vpn_enabled: bool = False,
        vpn_change_interval: int = 30,
        use_fake_ua: bool = True,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.rate_limit = rate_limit
        self.proxy_list = proxy_list or []
        self.vpn_enabled = vpn_enabled
        self.vpn_change_interval = vpn_change_interval
        self.use_fake_ua = use_fake_ua

        self._ua_cache: str | None = None
        self._proxy_index = 0
        self._vpn_process: subprocess.Popen | None = None
        self._downloaded_count = 0
        self._lock = threading.Lock()

    def get_random_user_agent(self) -> str:
        """Lấy User-Agent ngẫu nhiên."""
        if self.use_fake_ua and FAKE_USERAGENT_AVAILABLE:
            try:
                return fake_useragent.UserAgent().random
            except Exception:
                pass
        return self._get_fallback_user_agent()

    def _get_fallback_user_agent(self) -> str:
        """Danh sách User-Agent fallback."""
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        ]
        return random.choice(ua_list)

    def get_random_delay(self) -> float:
        """Lấy thời gian delay ngẫu nhiên."""
        return random.uniform(self.min_delay, self.max_delay)

    def get_next_proxy(self) -> str | None:
        """Lấy proxy tiếp theo trong danh sách (round-robin)."""
        if not self.proxy_list:
            return None
        with self._lock:
            proxy = self.proxy_list[self._proxy_index % len(self.proxy_list)]
            self._proxy_index += 1
            return proxy

    def should_change_vpn(self) -> bool:
        """Kiểm tra có nên đổi VPN không."""
        if not self.vpn_enabled:
            return False
        with self._lock:
            if self._downloaded_count >= self.vpn_change_interval:
                self._downloaded_count = 0
                return True
        return False

    def change_vpn(self, log_fn: Callable[[str], None] | None = None) -> bool:
        """Đổi VPN server (cần cài đặt VPN CLI)."""
        if not self.vpn_enabled:
            return False
        log = log_fn or (lambda x: None)

        vpn_apps = [
            ("nordvpn", ["-c", "--disconnect"]),
            ("expressvpn", ["disconnect"]),
            ("cyberghost", ["--disconnect"]),
        ]

        for cmd, args in vpn_apps:
            if shutil.which(cmd):
                try:
                    subprocess.run([cmd, "disconnect"], capture_output=True, timeout=10)
                    time.sleep(2)
                    subprocess.run([cmd, "-c"], capture_output=True, timeout=30)
                    log(f"[AntiBan] Đã đổi VPN server")
                    return True
                except Exception as e:
                    log(f"[AntiBan] Lỗi đổi VPN: {e}")
                    return False
        return False

    def increment_download_count(self) -> None:
        """Tăng số lượng đã tải."""
        with self._lock:
            self._downloaded_count += 1

    def create_ydl_opts(
        self,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Tạo yt-dlp options với Anti-Ban features."""
        opts = {
            "quiet": True,
            "rm_cachedir": True,
            "ratelimit": self.rate_limit,
            "http_headers": {
                "User-Agent": self.get_random_user_agent(),
            },
        }

        if self.proxy_list:
            opts["proxy"] = self.get_next_proxy()

        if extra_opts:
            opts.update(extra_opts)

        return opts


class ExponentialBackoff:
    """Exponential backoff cho việc thử lại."""

    def __init__(self, base_delay: float = 60, max_delay: float = 3600, max_retries: int = 3):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retry_count = 0

    def get_delay(self) -> float:
        """Lấy thời gian chờ tiếp theo."""
        delay = self.base_delay * (2 ** self.retry_count)
        return min(delay, self.max_delay)

    def should_retry(self) -> bool:
        """Kiểm tra có nên thử lại không."""
        return self.retry_count < self.max_retries

    def increment(self) -> None:
        """Tăng số lần thử."""
        self.retry_count += 1

    def reset(self) -> None:
        """Reset bộ đếm."""
        self.retry_count = 0


class FailedURLLogger:
    """Ghi log URL thất bại."""

    def __init__(self, log_file: str = "failed_urls.txt"):
        self.log_file = log_file

    def log_failed_url(self, url: str, error_code: str, error_message: str) -> None:
        """Ghi URL thất bại vào file."""
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{url}|{error_code}|{error_message}\n")
        except OSError:
            pass

    def load_failed_urls(self) -> list[tuple[str, str, str]]:
        """Đọc danh sách URL thất bại."""
        if not os.path.exists(self.log_file):
            return []
        failed = []
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("|")
                    if len(parts) >= 3:
                        failed.append((parts[0], parts[1], parts[2]))
        except OSError:
            pass
        return failed