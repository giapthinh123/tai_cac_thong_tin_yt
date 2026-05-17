from __future__ import annotations

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

def _load_stylesheet() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "thum.qss")
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
    ) -> None:
        super().__init__()
        self._url = url
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

        # Cài đặt UI
        setup_thumb_download_ui(self)

        # Kết nối tín hiệu
        self._video_folder_browse_btn.clicked.connect(self._pick_video_folder)
        self._folder_browse_btn.clicked.connect(self._pick_thumb_folder)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._cancel)
        self._btn_clear_idle.clicked.connect(self._clear_idle)
        self._cb_thumb.toggled.connect(lambda _c: self._sync_thumb_locale_widgets())
        self._sync_thumb_locale_widgets()

        # Cập nhật logic cho quality pills (chỉ chọn 1)
        for b in self._quality_buttons:
            b.clicked.connect(lambda checked, btn=b: self._on_quality_selected(btn) if checked else None)

    def _on_quality_selected(self, btn):
        for b in self._quality_buttons:
            if b != btn:
                b.setChecked(False)
        btn.setChecked(True)

    def _sync_thumb_locale_widgets(self) -> None:
        thumb_on = self._cb_thumb.isChecked()
        self._thumb_locale_label.setEnabled(thumb_on)
        self._cb_thumb_locale_en.setEnabled(thumb_on)
        self._cb_thumb_locale_ko.setEnabled(thumb_on)

    def _get_selected_quality(self) -> str:
        for b in self._quality_buttons:
            if b.isChecked():
                return b.text()
        return "Tốt nhất"

    def _pick_video_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu video")
        if path:
            self._video_folder_edit.setText(path)

    def _pick_thumb_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu thumbnail")
        if path:
            self._folder_edit.setText(path)

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
