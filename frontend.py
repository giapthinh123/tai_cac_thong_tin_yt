"""Bố cục giao diện tải thumbnail — tách khỏi logic (chỉ widget / layout)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


def _stat_card(value_name: str) -> tuple[QFrame, QLabel, QLabel]:
    card = QFrame()
    card.setObjectName("StatCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    val = QLabel("0")
    val.setObjectName(value_name)
    val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    sub = QLabel()
    sub.setObjectName("StatLabel")
    sub.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    lay.addWidget(val)
    lay.addWidget(sub)
    return card, val, sub


def setup_thumb_download_ui(win: QWidget) -> None:
    """Gắn layout lên `win` và gán các thuộc tính widget cho MainWindow."""
    win.setObjectName("RootChrome")


    head_left = QVBoxLayout()
    head_left.setSpacing(2)

    head_row = QHBoxLayout()
    head_row.addLayout(head_left, stretch=1)

    # —— Stats ——
    stat_row = QHBoxLayout()
    stat_row.setSpacing(10)

    c_total, v_total, l_total = _stat_card("StatValueTotal")
    l_total.setText("Tổng")
    c_dl, v_dl, l_dl = _stat_card("StatValueDl")
    l_dl.setText("Đang tải")
    c_ok, v_ok, l_ok = _stat_card("StatValueOk")
    l_ok.setText("Hoàn thành")
    c_er, v_er, l_er = _stat_card("StatValueErr")
    l_er.setText("Lỗi")

    for w in (c_total, c_dl, c_ok, c_er):
        stat_row.addWidget(w, stretch=1)

    # —— THÊM URL ——
    url_card = _card()
    url_outer = QVBoxLayout(url_card)
    url_outer.setContentsMargins(16, 14, 16, 14)
    url_outer.setSpacing(12)

    sec_url = QLabel("THÊM URL")
    sec_url.setObjectName("SectionTitle")

    url_edit = QLineEdit()
    url_edit.setPlaceholderText("Dán URL video / playlist / kênh YouTube…")
    add_one = QPushButton("+ Thêm")
    add_one.setObjectName("AccentAdd")
    add_one.setToolTip("Điền URL từ clipboard vào ô phía trên (YouTube).")

    row1 = QHBoxLayout()
    row1.setSpacing(10)
    row1.addWidget(url_edit, stretch=1)
    row1.addWidget(add_one)

    url_scan = QLabel("")
    url_scan.setObjectName("UrlScanHint")
    url_scan.setWordWrap(True)

    url_outer.addWidget(sec_url)
    url_outer.addLayout(row1)
    url_outer.addWidget(url_scan)

    # —— CÀI ĐẶT ——
    set_card = _card()
    set_outer = QVBoxLayout(set_card)
    set_outer.setContentsMargins(16, 14, 16, 14)
    set_outer.setSpacing(12)

    sec_set = QLabel("CÀI ĐẶT")
    sec_set.setObjectName("SectionTitle")

    video_folder = QLineEdit(r"D:\downloads\videos")
    thumb_folder = QLineEdit()
    thumb_folder.setPlaceholderText("Chọn thư mục lưu thumbnail")

    btn_pick_video = QPushButton("Chọn")
    btn_pick_thumb = QPushButton("Chọn")

    def folder_row(label: str, edit: QLineEdit, btn: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        ic = QLabel("📁")
        ic.setStyleSheet("font-size: 14px;")
        row.addWidget(ic, alignment=Qt.AlignVCenter)
        row.addWidget(QLabel(label), alignment=Qt.AlignVCenter)
        row.addWidget(edit, stretch=1)
        row.addWidget(btn)
        return row

    set_outer.addWidget(sec_set)
    set_outer.addLayout(folder_row("Thư mục video", video_folder, btn_pick_video))
    set_outer.addLayout(folder_row("Thư mục thumb", thumb_folder, btn_pick_thumb))

    qual_row = QHBoxLayout()
    qual_row.setSpacing(8)
    qual_lab = QLabel("Chất lượng video:")
    qual_lab.setStyleSheet("color:#374151;font-size:13px;")
    qual_row.addWidget(qual_lab)
    quality_buttons = []
    for t in ("360p", "480p", "720p", "1080p", "Tốt nhất"):
        b = QPushButton(t)
        b.setObjectName("QualityPill")
        b.setCheckable(True)
        if t == "Tốt nhất":
            b.setChecked(True)
        qual_row.addWidget(b)
        quality_buttons.append(b)
    qual_row.addStretch(1)

    chk_row = QHBoxLayout()
    chk_row.setSpacing(18)
    cb_video = QCheckBox("Video")
    cb_video.setChecked(True)
    cb_thumb = QCheckBox("Thumbnail")
    cb_thumb.setChecked(True)
    chk_row.addWidget(cb_video)
    chk_row.addWidget(cb_thumb)
    chk_row.addStretch(1)

    thumb_loc_row = QHBoxLayout()
    thumb_loc_row.setSpacing(14)
    thumb_loc_lab = QLabel("Thumb — tên file theo ngôn ngữ:")
    thumb_loc_lab.setStyleSheet("color:#374151;font-size:13px;")
    cb_thumb_locale_en = QCheckBox("Tiếng Anh (en)")
    cb_thumb_locale_ko = QCheckBox("Tiếng Hàn (ko)")
    cb_thumb_locale_en.setChecked(True)
    cb_thumb_locale_ko.setChecked(True)
    thumb_loc_row.addWidget(thumb_loc_lab, alignment=Qt.AlignVCenter)
    thumb_loc_row.addWidget(cb_thumb_locale_en, alignment=Qt.AlignVCenter)
    thumb_loc_row.addWidget(cb_thumb_locale_ko, alignment=Qt.AlignVCenter)
    thumb_loc_row.addStretch(1)

    conc_row = QHBoxLayout()
    conc_row.setSpacing(8)
    conc_lab = QLabel("Tải đồng thời tối đa")
    conc_lab.setStyleSheet("color:#374151;font-size:13px;")
    spin = QSpinBox()
    spin.setRange(1, 16)
    spin.setValue(2)
    suffix = QLabel("mục cùng lúc")
    suffix.setStyleSheet("color:#6b7280;font-size:12px;")
    conc_row.addWidget(conc_lab)
    conc_row.addWidget(spin)
    conc_row.addWidget(suffix)
    conc_row.addStretch(1)

    set_outer.addLayout(qual_row)
    set_outer.addLayout(chk_row)
    set_outer.addLayout(thumb_loc_row)
    set_outer.addLayout(conc_row)

    # —— HÀNG ĐỢI ——
    queue_card = _card()
    queue_outer = QVBoxLayout(queue_card)
    queue_outer.setContentsMargins(16, 14, 16, 14)
    queue_outer.setSpacing(10)

    q_head = QHBoxLayout()
    sec_q = QLabel("HÀNG ĐỢI")
    sec_q.setObjectName("SectionTitle")
    queue_count = QLabel("0 / 0 mục")
    queue_count.setObjectName("QueueCount")

    q_head.addWidget(sec_q, alignment=Qt.AlignVCenter)
    q_head.addStretch(1)
    q_head.addWidget(queue_count, alignment=Qt.AlignVCenter)

    stack = QStackedWidget()
    stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    empty_page = QWidget()
    empty_lay = QVBoxLayout(empty_page)
    empty_lay.setAlignment(Qt.AlignCenter)
    hint = QLabel("Chưa có mục nào trong hàng đợi")
    hint.setObjectName("QueueHint")
    hint.setAlignment(Qt.AlignCenter)
    empty_lay.addStretch(1)
    empty_lay.addWidget(hint)
    empty_lay.addStretch(2)

    active_page = QWidget()
    active_lay = QVBoxLayout(active_page)
    active_lay.setContentsMargins(0, 4, 0, 0)
    active_lay.setSpacing(8)
    task_title = QLabel("Đang tải…")
    task_title.setStyleSheet("font-size:13px;font-weight:600;color:#111827;")
    progress = QProgressBar()
    progress.setRange(0, 1)
    progress.setValue(0)
    progress.setFormat("%p% — %v / %m")
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMinimumHeight(160)
    active_lay.addWidget(task_title)
    active_lay.addWidget(progress)
    active_lay.addWidget(QLabel("Nhật ký"))
    active_lay.addWidget(log, stretch=1)

    stack.addWidget(empty_page)
    stack.addWidget(active_page)

    queue_outer.addLayout(q_head)
    queue_outer.addWidget(stack, stretch=1)

    # —— Footer ——
    foot = QHBoxLayout()
    foot.setSpacing(12)
    btn_clear = QPushButton("Xóa tất cả")
    btn_clear.setObjectName("GhostToolbar")
    btn_clear.setToolTip("Xóa URL và nhật ký (khi không đang tải).")
    start_btn = QPushButton("Bắt đầu tất cả")
    start_btn.setObjectName("PrimaryFooter")
    start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    cancel_btn = QPushButton("Dừng")
    cancel_btn.setObjectName("DangerFooter")
    cancel_btn.setEnabled(False)
    cancel_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    foot.addWidget(btn_clear)
    foot.addWidget(start_btn, stretch=1)
    foot.addWidget(cancel_btn, stretch=1)

    root = QVBoxLayout(win)
    root.setContentsMargins(18, 16, 18, 18)
    root.setSpacing(14)
    root.addLayout(head_row)
    root.addLayout(stat_row)
    root.addWidget(url_card)
    root.addWidget(set_card)
    root.addWidget(queue_card, stretch=1)
    root.addLayout(foot)

    # Expose on host window (MainWindow)
    win._url_edit = url_edit
    win._folder_edit = thumb_folder
    win._folder_browse_btn = btn_pick_thumb
    win._start_btn = start_btn
    win._cancel_btn = cancel_btn
    win._progress = progress
    win._log = log

    win._stat_total_val = v_total
    win._stat_dl_val = v_dl
    win._stat_ok_val = v_ok
    win._stat_err_val = v_er

    win._queue_stack = stack
    win._queue_count_label = queue_count
    win._queue_task_title = task_title

    win._btn_clipboard_url = add_one
    win._btn_clear_idle = btn_clear
    win._url_scan_status = url_scan

    win._video_folder_edit = video_folder
    win._video_folder_browse_btn = btn_pick_video
    win._quality_buttons = quality_buttons
    win._cb_video = cb_video
    win._cb_thumb = cb_thumb
    win._thumb_locale_label = thumb_loc_lab
    win._cb_thumb_locale_en = cb_thumb_locale_en
    win._cb_thumb_locale_ko = cb_thumb_locale_ko
    win._thread_spin = spin
