#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import (
    QObject,
    Qt,
    QThread,
    pyqtSignal,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qr_scanner_core import (
    QRDependencyError,
    FileScanResult,
    ScanOptions,
    append_log_line,
    scan_and_move_qr,
)


class ScanWorker(QObject):

    progress = pyqtSignal(int, int, FileScanResult)
    log_line = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal(dict)

    def __init__(self, root: Path, options: ScanOptions):
        super().__init__()
        self.root = root
        self.options = options
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _is_cancelled(self) -> bool:
        return self._cancel_requested

    def run(self) -> None:
        try:
            def on_log(message: str, log_dir: Path) -> None:
                append_log_line(log_dir, message)
                self.log_line.emit(message)

            def on_progress(
                current: int, total: int, result: FileScanResult
            ) -> None:
                self.progress.emit(current, total, result)

            stats = scan_and_move_qr(
                self.root,
                self.options,
                on_progress=on_progress,
                on_log=on_log,
                is_cancelled=self._is_cancelled,
            )

            if self._cancel_requested:
                self.cancelled.emit(stats)
            else:
                self.finished.emit(stats)
        except QRDependencyError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")


class ResultItemWidget(QWidget):

    def __init__(self, result: FileScanResult, root: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.result = result
        self.root = root
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        thumb_label = QLabel()
        thumb_label.setFixedSize(64, 64)
        thumb_label.setScaledContents(True)
        pix = QPixmap(str(self.result.src_path))
        if pix.isNull():
            thumb_label.setText("No\nPreview")
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            thumb_label.setPixmap(pix.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        text_layout = QVBoxLayout()

        fname_label = QLabel(self.result.src_path.name)
        fname_label.setStyleSheet("font-weight: bold;")

        rel_path = str(self.result.src_path.relative_to(self.root)) if self.result.src_path.is_relative_to(self.root) else str(self.result.src_path)
        path_label = QLabel(rel_path)
        path_label.setStyleSheet("color: #555555;")

        status_parts: List[str] = []
        if self.result.error:
            status_parts.append(f"Error: {self.result.error}")
        elif self.result.had_qr:
            if self.result.moved:
                status_parts.append("Moved")
            else:
                status_parts.append("QR found")
        else:
            status_parts.append("No QR")

        if self.result.dest_path is not None:
            status_parts.append(f"→ {self.result.dest_path.name}")

        status_label = QLabel(" | ".join(status_parts))

        if self.result.qr_values:
            tooltip_text = "Decoded QR value(s):\n" + "\n".join(self.result.qr_values)
            fname_label.setToolTip(tooltip_text)
            path_label.setToolTip(tooltip_text)
            status_label.setToolTip(tooltip_text)
            thumb_label.setToolTip(tooltip_text)

        text_layout.addWidget(fname_label)
        text_layout.addWidget(path_label)
        text_layout.addWidget(status_label)

        layout.addWidget(thumb_label)
        layout.addLayout(text_layout)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QR Image Sorter")
        self.resize(960, 640)

        self._thread: Optional[QThread] = None
        self._worker: Optional[ScanWorker] = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        controls_box = QGroupBox("Scan Settings")
        controls_layout = QGridLayout(controls_box)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(6)

        folder_label = QLabel("Folder:")
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select a folder to scan…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.select_folder)

        controls_layout.addWidget(folder_label, 0, 0)
        controls_layout.addWidget(self.folder_edit, 0, 1)
        controls_layout.addWidget(browse_btn, 0, 2)

        self.recursive_cb = QCheckBox("Recursive")
        self.recursive_cb.setChecked(True)
        self.dry_run_cb = QCheckBox("Dry run (no changes)")
        self.dry_run_cb.setChecked(False)
        self.preserve_ts_cb = QCheckBox("Preserve timestamps")
        self.preserve_ts_cb.setChecked(True)

        controls_layout.addWidget(self.recursive_cb, 1, 0)
        controls_layout.addWidget(self.dry_run_cb, 1, 1)
        controls_layout.addWidget(self.preserve_ts_cb, 1, 2)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self.start_scan)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)

        controls_layout.addWidget(self.scan_btn, 2, 1)
        controls_layout.addWidget(self.stop_btn, 2, 2)

        main_layout.addWidget(controls_box)

        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)

        self.status_label = QLabel("Idle")
        self.status_label.setMinimumWidth(220)

        progress_layout.addWidget(self.progress_bar, stretch=3)
        progress_layout.addWidget(self.status_label, stretch=2)

        main_layout.addLayout(progress_layout)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)

        results_box = QGroupBox("Results")
        results_layout = QVBoxLayout(results_box)
        self.results_list = QListWidget()
        self.results_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        results_layout.addWidget(self.results_list)

        splitter.addWidget(results_box)

        log_box = QGroupBox("Log (last 500 lines)")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        log_layout.addWidget(self.log_view)

        splitter.addWidget(log_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter, stretch=1)

        self.setStatusBar(QStatusBar())

        self._log_buffer: List[str] = []
        self._current_total = 0

    def append_log(self, line: str) -> None:
        self._log_buffer.append(line)
        if len(self._log_buffer) > 500:
            self._log_buffer = self._log_buffer[-500:]
        self.log_view.setPlainText("\n".join(self._log_buffer))
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def select_folder(self) -> None:
        dlg = QFileDialog(self, "Select folder to scan")
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dlg.exec():
            folders = dlg.selectedFiles()
            if folders:
                self.folder_edit.setText(folders[0])

    def start_scan(self) -> None:
        if self._thread is not None:
            return

        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            QMessageBox.warning(self, "No folder", "Please select a folder to scan.")
            return

        root = Path(folder_text).expanduser()
        if not root.exists() or not root.is_dir():
            QMessageBox.critical(self, "Invalid folder", f"Folder does not exist: {root}")
            return

        options = ScanOptions(
            recursive=self.recursive_cb.isChecked(),
            dry_run=self.dry_run_cb.isChecked(),
            preserve_timestamps=self.preserve_ts_cb.isChecked(),
        )

        self.results_list.clear()
        self._log_buffer.clear()
        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting scan…")
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage("Scanning…")

        self._thread = QThread()
        self._worker = ScanWorker(root=root, options=options)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.on_progress)
        self._worker.log_line.connect(self.append_log)
        self._worker.finished.connect(self.on_finished)
        self._worker.cancelled.connect(self.on_cancelled)
        self._worker.error.connect(self.on_error)

        self._worker.finished.connect(self._cleanup_thread)
        self._worker.cancelled.connect(self._cleanup_thread)
        self._worker.error.connect(self._cleanup_thread)

        self._thread.start()

    def stop_scan(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.status_label.setText("Stopping…")
            self.statusBar().showMessage("Stopping (will finish current file)…")

    def _cleanup_thread(self, *_args) -> None:
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.statusBar().clearMessage()

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None

    def on_progress(self, current: int, total: int, result: FileScanResult) -> None:
        self._current_total = total

        if total > 0:
            percent = int(current / total * 100)
        else:
            percent = 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"Scanning {current} / {total}")

        item = QListWidgetItem()
        item_widget = ResultItemWidget(result, root=Path(self.folder_edit.text().strip()))
        item.setSizeHint(item_widget.sizeHint())
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, item_widget)

    def on_finished(self, stats: dict) -> None:
        self.status_label.setText(
            f"Completed. Total: {stats.get('total', 0)}, "
            f"QR: {stats.get('with_qr', 0)}, "
            f"Moved: {stats.get('moved', 0)}, "
            f"No QR: {stats.get('no_qr', 0)}, "
            f"Errors: {stats.get('errors', 0)}"
        )
        self.statusBar().showMessage("Scan complete", 5000)
        QMessageBox.information(
            self,
            "Scan complete",
            (
                f"Scan complete.\n\n"
                f"Total files: {stats.get('total', 0)}\n"
                f"With QR: {stats.get('with_qr', 0)}\n"
                f"Moved: {stats.get('moved', 0)}\n"
                f"No QR: {stats.get('no_qr', 0)}\n"
                f"Errors: {stats.get('errors', 0)}"
            ),
        )

    def on_cancelled(self, stats: dict) -> None:
        self.status_label.setText(
            f"Cancelled. Processed: {self._current_total - stats.get('skipped', 0)}, "
            f"Skipped: {stats.get('skipped', 0)}"
        )
        self.statusBar().showMessage("Scan cancelled", 5000)

    def on_error(self, message: str) -> None:
        self.status_label.setText("Error")
        self.statusBar().showMessage("Error during scan", 5000)
        QMessageBox.critical(self, "Error", message)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

