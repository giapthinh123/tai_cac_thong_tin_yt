from __future__ import annotations

import json
import os
import sys
import threading

from PyQt5.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QWidget,
)

from frontend import setup_thumb_download_ui
from yt_dowload_video import download_job_batch


import sys
import os

def get_resource_path(relative_path):
    """Lấy đường dẫn tuyệt đối tới file, hoạt động cả khi code và khi chạy file .exe"""
    try:
        # sys._MEIPASS là biến môi trường PyInstaller tạo ra chứa đường dẫn thư mục tạm
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def _get_settings_path() -> str:
    return get_resource_path("settings.json")

def _load_settings() -> dict:
    path = _get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "video_folder": "",
        "thumb_folder": "",
        "quality": "720p",
        "download_video": True,
        "download_thumb": True,
        "thumb_locale_en": True,
        "thumb_locale_ko": True,
        "thumb_locale_ja": True,
        "custom_locale": "",
        "concurrent_count": 2
    }

def _save_settings(settings: dict) -> None:
    path = _get_settings_path()
    with open(path, encoding="utf-8", mode="w") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def _load_stylesheet() -> str:
    # Sử dụng hàm get_resource_path để lấy đúng vị trí file
    path = get_resource_path("thum.qss")
    with open(path, encoding="utf-8") as f:
        return f.read()


class DownloadWorker(QObject):
    log_line = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        video_folder: str,
        thumb_folder: str,
        download_video: bool,
        download_thumb: bool,
        quality: str,
        thumb_locales: list[str],
        cancel_event: threading.Event,
        anti_ban_config: dict | None = None,
        max_workers: int = 1,
        retry_urls: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._retry_urls = retry_urls or []
        self._video_folder = video_folder
        self._thumb_folder = thumb_folder
        self._download_video = download_video
        self._download_thumb = download_thumb
        self._quality = quality
        self._thumb_locales = thumb_locales
        self._cancel_event = cancel_event
        self._anti_ban_config = anti_ban_config
        self._max_workers = max_workers

    def run(self) -> None:
        try:
            if self._retry_urls:
                total_ok, total_fail = 0, 0
                for i, retry_url in enumerate(self._retry_urls, 1):
                    if self._cancel_event.is_set():
                        break
                    self.log_line.emit(f"[Retry {i}/{len(self._retry_urls)}] {retry_url}")
                    try:
                        ok, fail = download_job_batch(
                            retry_url,
                            self._video_folder,
                            self._thumb_folder,
                            download_video=self._download_video,
                            download_thumb=self._download_thumb,
                            quality=self._quality,
                            thumb_locales=self._thumb_locales,
                            on_progress=lambda c, t: self.progress.emit(c + (i-1) * t, len(self._retry_urls) * t),
                            on_log=self.log_line.emit,
                            cancelled=self._cancel_event.is_set,
                            anti_ban_config=self._anti_ban_config,
                            max_workers=self._max_workers,
                        )
                        total_ok += ok
                        total_fail += fail
                    except Exception as e:
                        self.log_line.emit(f"[Lỗi] {retry_url}: {e}")
                        total_fail += 1
                self.finished.emit(total_ok, total_fail)
            else:
                ok, fail = download_job_batch(
                    self._url,
                    self._video_folder,
                    self._thumb_folder,
                    download_video=self._download_video,
                    download_thumb=self._download_thumb,
                    quality=self._quality,
                    thumb_locales=self._thumb_locales,
                    on_progress=self.progress.emit,
                    on_log=self.log_line.emit,
                    cancelled=self._cancel_event.is_set,
                    anti_ban_config=self._anti_ban_config,
                    max_workers=self._max_workers,
                )
                self.finished.emit(ok, fail)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YouTube Downloader")
        self.resize(800, 600)

        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._cancel_event = threading.Event()
        self._retry_urls: list[str] = []

        # Cài đặt UI
        setup_thumb_download_ui(self)

        # Load settings
        self._load_settings_from_file()

        # Kết nối tín hiệu
        self._video_folder_browse_btn.clicked.connect(self._pick_video_folder)
        self._folder_browse_btn.clicked.connect(self._pick_thumb_folder)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._cancel)
        self._retry_btn.clicked.connect(self._retry_failed)
        self._btn_clear_idle.clicked.connect(self._clear_idle)
        self._cb_thumb.toggled.connect(lambda _c: self._sync_thumb_locale_widgets())
        self._sync_thumb_locale_widgets()

        # Cập nhật logic cho quality pills (chỉ chọn 1)
        for b in self._quality_buttons:
            b.clicked.connect(lambda checked, btn=b: self._on_quality_selected(btn))

        # Lưu settings khi thay đổi
        self._cb_video.toggled.connect(lambda _: self._save_settings_to_file())
        self._cb_thumb.toggled.connect(lambda _: self._save_settings_to_file())
        self._cb_thumb_locale_en.toggled.connect(lambda _: self._save_settings_to_file())
        self._cb_thumb_locale_ko.toggled.connect(lambda _: self._save_settings_to_file())
        self._cb_thumb_locale_ja.toggled.connect(lambda _: self._save_settings_to_file())
        self._custom_locale_edit.textChanged.connect(lambda _: self._save_settings_to_file())
        self._thread_spin.valueChanged.connect(lambda _: self._save_settings_to_file())

    def _on_quality_selected(self, btn):
        for b in self._quality_buttons:
            if b != btn:
                b.setChecked(False)
        btn.setChecked(True)
        self._save_settings_to_file()

    def _sync_thumb_locale_widgets(self) -> None:
        thumb_on = self._cb_thumb.isChecked()
        self._thumb_locale_label.setEnabled(thumb_on)
        self._cb_thumb_locale_en.setEnabled(thumb_on)
        self._cb_thumb_locale_ko.setEnabled(thumb_on)
        self._cb_thumb_locale_ja.setEnabled(thumb_on)

    def _get_selected_quality(self) -> str:
        for b in self._quality_buttons:
            if b.isChecked():
                return b.text()
        return "Tốt nhất"

    def _load_settings_from_file(self) -> None:
        s = _load_settings()
        self._video_folder_edit.setText(s.get("video_folder", ""))
        self._folder_edit.setText(s.get("thumb_folder", ""))
        self._cb_video.setChecked(s.get("download_video", True))
        self._cb_thumb.setChecked(s.get("download_thumb", True))
        self._cb_thumb_locale_en.setChecked(s.get("thumb_locale_en", True))
        self._cb_thumb_locale_ko.setChecked(s.get("thumb_locale_ko", True))
        self._cb_thumb_locale_ja.setChecked(s.get("thumb_locale_ja", True))
        self._custom_locale_edit.setText(s.get("custom_locale", ""))
        self._thread_spin.setValue(s.get("concurrent_count", 2))
        quality = s.get("quality", "720p")
        for b in self._quality_buttons:
            b.setChecked(b.text() == quality)

    def _save_settings_to_file(self) -> None:
        settings = {
            "video_folder": self._video_folder_edit.text(),
            "thumb_folder": self._folder_edit.text(),
            "quality": self._get_selected_quality(),
            "download_video": self._cb_video.isChecked(),
            "download_thumb": self._cb_thumb.isChecked(),
            "thumb_locale_en": self._cb_thumb_locale_en.isChecked(),
            "thumb_locale_ko": self._cb_thumb_locale_ko.isChecked(),
            "thumb_locale_ja": self._cb_thumb_locale_ja.isChecked(),
            "custom_locale": self._custom_locale_edit.text().strip(),
            "concurrent_count": self._thread_spin.value()
        }
        _save_settings(settings)

    def closeEvent(self, event) -> None:
        self._save_settings_to_file()
        event.accept()

    def _pick_video_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu video")
        if path:
            self._video_folder_edit.setText(path)
            self._save_settings_to_file()

    def _pick_thumb_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu thumbnail")
        if path:
            self._folder_edit.setText(path)
            self._save_settings_to_file()

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(max(1, total))
        self._progress.setValue(current)
        self._queue_count_label.setText(f"{current} / {total} mục")

    def _clear_idle(self):
        self._url_edit.clear()
        self._log.clear()
        self._progress.setValue(0)
        self._queue_count_label.setText("0 / 0 mục")

    def _retry_failed(self) -> None:
        from anti_ban import FailedURLLogger
        logger = FailedURLLogger()
        failed = logger.load_failed_urls()
        if not failed:
            QMessageBox.information(self, "Không có mục thất bại", "Không tìm thấy URL nào bị thất bại.")
            return
        self._retry_urls = [item[0] for item in failed]
        reply = QMessageBox.question(
            self,
            "Xác nhận tải lại",
            f"Tìm thấy {len(self._retry_urls)} mục bị thất bại. Bạn có muốn tải lại không?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._url_edit.setText(self._retry_urls[0])
        self._log.append(f"[Retry] Sẽ tải lại {len(self._retry_urls)} mục...")
        self._start()

    def _start(self) -> None:
        url = self._url_edit.text().strip()
        v_folder = self._video_folder_edit.text().strip()
        t_folder = self._folder_edit.text().strip()
        
        dl_video = self._cb_video.isChecked()
        dl_thumb = self._cb_thumb.isChecked()

        if not url:
            QMessageBox.warning(self, "Thiếu URL", "Vui lòng nhập URL YouTube.")
            return

        if not dl_video and not dl_thumb:
            QMessageBox.warning(self, "Chưa chọn loại tải", "Vui lòng chọn tải Video hoặc Thumbnail.")
            return

        if dl_video and not v_folder:
            QMessageBox.warning(self, "Thiếu thư mục", "Vui lòng chọn thư mục lưu video.")
            return

        if dl_thumb and not t_folder:
            QMessageBox.warning(self, "Thiếu thư mục", "Vui lòng chọn thư mục lưu thumbnail.")
            return

        quality = self._get_selected_quality()

        thumb_locales: list[str] = []
        if dl_thumb:
            if self._cb_thumb_locale_en.isChecked():
                thumb_locales.append("en")
            if self._cb_thumb_locale_ko.isChecked():
                thumb_locales.append("ko")
            if self._cb_thumb_locale_ja.isChecked():
                thumb_locales.append("ja")
            custom_input = self._custom_locale_edit.text().strip()
            if custom_input:
                for code in custom_input.split(","):
                    code = code.strip().lower()
                    if code and code not in thumb_locales:
                        thumb_locales.append(code)

        self._cancel_event.clear()
        self._log.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._queue_count_label.setText("0 / ? mục")
        
        # Switch stack widget to active page
        self._queue_stack.setCurrentIndex(1)
        
        self._queue_task_title.setText("Đang tải...")

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        proxy_list = []
        proxy_text = self._proxy_edit.text().strip() if hasattr(self, '_proxy_edit') else ""
        if proxy_text:
            proxy_list = [p.strip() for p in proxy_text.split(",") if p.strip()]

        max_workers = self._thread_spin.value() if hasattr(self, '_thread_spin') else 1

        anti_ban_config = {
            "min_delay": 5.5,
            "max_delay": 15.0,
            "rate_limit": 5_000_000,
            "proxy_list": proxy_list if proxy_list else None,
            "vpn_enabled": self._cb_vpn.isChecked() if hasattr(self, '_cb_vpn') else False,
            "vpn_change_interval": 30,
            "use_fake_ua": True,
            "max_retries": 3,
        }

        if max_workers > 1:
            self._log.appendPlainText(f"Sử dụng {max_workers} threads để tải song song")

        self._thread = QThread()
        self._worker = DownloadWorker(
            url=url,
            video_folder=v_folder,
            thumb_folder=t_folder,
            download_video=dl_video,
            download_thumb=dl_thumb,
            quality=quality,
            thumb_locales=thumb_locales,
            cancel_event=self._cancel_event,
            anti_ban_config=anti_ban_config,
            max_workers=max_workers,
            retry_urls=self._retry_urls if self._retry_urls else None,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_line.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)

        self._thread.start()

    def _cancel(self) -> None:
        self._cancel_event.set()
        self._append_log("(Đang hủy sau video hiện tại…)")
        self._cancel_btn.setEnabled(False)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        self._worker = None
        self._retry_urls = []
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _on_finished(self, ok: int, fail: int) -> None:
        total = int(self._stat_total_val.text())
        total_ok = int(self._stat_ok_val.text()) + ok
        total_err = int(self._stat_err_val.text()) + fail
        
        self._stat_total_val.setText(str(total + ok + fail))
        self._stat_ok_val.setText(str(total_ok))
        self._stat_err_val.setText(str(total_err))

        QMessageBox.information(
            self,
            "Hoàn tất",
            f"Tải xong.\nThành công: {ok}\nThất bại: {fail}",
        )
        self._queue_task_title.setText("Hoàn thành")

    def _on_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Lỗi", message)
        self._queue_task_title.setText("Có lỗi xảy ra")

    def closeEvent(self, event) -> None:
        self._cancel_event.set()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(5000)
        event.accept()


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(_load_stylesheet())
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
