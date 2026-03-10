from __future__ import annotations

import json
import os
import sys
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from musiclaw.config import AppConfig, CacheConfig, LlmConfig, MatchingConfig, NetworkConfig, RootConfig, SourcesConfig, TagsConfig
from musiclaw.config import ProcessingConfig
from musiclaw.models import AlbumProcessingResult, DecisionAction, LocalAlbum, MatchStatus, RunReport, SearchOverrides
from musiclaw.pipeline import MusicLawPipeline
from musiclaw.reporter import load_report, render_console_summary, save_report
from musiclaw.scanner import scan_music_root
from musiclaw.sources.vocadb import clear_vocadb_csv_cache

QT_API = None
QT_IMPORT_ERROR: Exception | None = None

try:  # pragma: no cover - runtime import path
    from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    QT_API = "PySide6"
except ImportError as exc:  # pragma: no cover - runtime import path
    QT_IMPORT_ERROR = exc
    try:
        from PyQt5.QtCore import QObject, QSettings, Qt, QThread, pyqtSignal
        from PyQt5.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGroupBox,
            QHeaderView,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QSplitter,
            QSpinBox,
            QTableWidget,
            QTableWidgetItem,
            QTabWidget,
            QVBoxLayout,
            QWidget,
        )

        Signal = pyqtSignal
        QT_API = "PyQt5"
        QT_IMPORT_ERROR = None
    except ImportError as exc2:
        QT_IMPORT_ERROR = exc2


if QT_API is not None:
    class PipelineWorker(QObject):
        finished = Signal(object)
        failed = Signal(str)

        def __init__(
            self,
            mode: str,
            config: AppConfig,
            root: Path | None = None,
            report: RunReport | None = None,
            album: LocalAlbum | None = None,
            overrides: SearchOverrides | None = None,
            album_inputs: list[tuple[LocalAlbum, SearchOverrides]] | None = None,
        ) -> None:
            super().__init__()
            self.mode = mode
            self.config = config
            self.root = root
            self.report = report
            self.album = album
            self.overrides = overrides
            self.album_inputs = album_inputs or []

        def run(self) -> None:
            pipeline = MusicLawPipeline(self.config)
            try:
                if self.mode == "match":
                    assert self.root is not None
                    output = pipeline.run_report(self.root, mode="match")
                elif self.mode == "match-batch":
                    assert self.root is not None
                    output = pipeline.match_with_overrides(self.root, self.album_inputs)
                elif self.mode == "apply":
                    assert self.report is not None
                    output = pipeline.apply_from_report(self.report)
                elif self.mode == "match-album":
                    assert self.album is not None
                    output = pipeline.match_album(self.album, self.overrides)
                else:
                    raise ValueError(f"Unsupported worker mode: {self.mode}")
                self.finished.emit(output)
            except Exception:
                self.failed.emit(traceback.format_exc())
            finally:
                pipeline.close()


    class MusicLawMainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"musiclaw GUI ({QT_API})")
            self.resize(1440, 920)
            self.settings = QSettings("musiclaw", "musiclaw-gui")
            self.current_report: RunReport | None = None
            self.current_row: int = -1
            self.current_album_row: int = -1
            self.album_inventory: list[LocalAlbum] = []
            self.album_overrides: dict[str, SearchOverrides] = {}
            self._updating_album_table = False
            self._worker_thread: QThread | None = None
            self._worker: PipelineWorker | None = None

            self.root_edit = QLineEdit()
            self.report_edit = QLineEdit("reports/latest.json")
            self.apply_output_edit = QLineEdit("reports/apply.json")
            self.dizzylab_check = QCheckBox("dizzylab")
            self.vocadb_check = QCheckBox("VocaDB")
            self.vcpedia_check = QCheckBox("VCPedia")
            self.max_candidates_spin = QSpinBox()
            self.album_workers_spin = QSpinBox()
            self.query_workers_spin = QSpinBox()
            self.parallel_profile_combo = QComboBox()
            self.parallel_hint_label = QLabel()
            self.auto_score_spin = QDoubleSpinBox()
            self.review_score_spin = QDoubleSpinBox()
            self.llm_enabled_check = QCheckBox("Enable LLM")
            self.llm_base_edit = QLineEdit()
            self.llm_key_edit = QLineEdit()
            self.llm_model_edit = QLineEdit()
            self.override_album_title_edit = QLineEdit()
            self.priority_urls_edit = QPlainTextEdit()
            self.manual_text_edit = QPlainTextEdit()
            self.manual_urls_only_check = QCheckBox("Only use priority URLs")
            self.rematch_selected_button = QPushButton("Re-match Selected")
            self.open_priority_url_button = QPushButton("Open Priority URL")
            self.open_album_folder_button = QPushButton("Open Album Folder")
            self.reviewer_edit = QLineEdit()
            self.manual_verified_check = QCheckBox("Verified for apply")
            self.approved_action_combo = QComboBox()
            self.manual_notes_edit = QPlainTextEdit()
            self.summary_text = QPlainTextEdit()
            self.json_text = QPlainTextEdit()
            self.evidence_preview_text = QPlainTextEdit()
            self.comparison_text = QPlainTextEdit()
            self.tracks_table = QTableWidget(0, 4)
            self.evidence_table = QTableWidget(0, 3)
            self.history_table = QTableWidget(0, 5)
            self.results_table = QTableWidget(0, 7)
            self.album_table = QTableWidget(0, 5)
            self.meta_label = QLabel("No report loaded")
            self.match_button = QPushButton("Run Match")
            self.load_button = QPushButton("Load Report")
            self.save_button = QPushButton("Save Report")
            self.apply_button = QPushButton("Apply Verified")
            self.import_config_button = QPushButton("Import Config")
            self.export_config_button = QPushButton("Export Config")
            self.clear_vocadb_csv_button = QPushButton("Clear VocaDB CSV Cache")
            self.prev_button = QPushButton("Prev")
            self.next_button = QPushButton("Next")
            self.approve_selected_button = QPushButton("Approve Selected")
            self.approve_ready_button = QPushButton("Approve Ready")
            self.skip_selected_button = QPushButton("Skip Selected")
            self.skip_not_found_button = QPushButton("Skip Not Found")

            self._build_ui()
            self._restore_state()
            self._wire_events()

        def _build_ui(self) -> None:
            central = QWidget()
            self.setCentralWidget(central)
            root_layout = QVBoxLayout(central)

            root_layout.addWidget(self._build_settings_group())
            root_layout.addWidget(self._build_actions_group())
            root_layout.addWidget(self.meta_label)
            root_layout.addWidget(self._build_results_splitter(), stretch=1)

            self.summary_text.setReadOnly(True)
            self.json_text.setReadOnly(True)
            self.evidence_preview_text.setReadOnly(True)
            self.comparison_text.setReadOnly(True)
            self.manual_notes_edit.setPlaceholderText("Optional reviewer notes for the selected album")
            self.priority_urls_edit.setPlaceholderText("One priority URL per line. These pages are fetched before site search.")
            self.manual_text_edit.setPlaceholderText(
                "Optional raw notes or copied text. Examples: '专辑: Foo', '歌手 Singer A', '01. Song A - Singer A', 'M1 曲名 / Vocal'."
            )
            self.manual_text_edit.setToolTip(
                "Manual raw text is primary evidence. Short, messy, or shorthand notes are okay; try lines like 'Title: Foo', 'Circle: Bar', '01. Song - Vocal'."
            )
            self.override_album_title_edit.setPlaceholderText("Manual album title override used for search")
            self.manual_urls_only_check.setToolTip("When enabled, re-match will fetch only the priority URLs and skip normal site search")
            self.llm_key_edit.setEchoMode(QLineEdit.Password)

            self.max_candidates_spin.setRange(1, 20)
            self.max_candidates_spin.setValue(5)
            self.album_workers_spin.setRange(1, 16)
            self.album_workers_spin.setValue(4)
            self.query_workers_spin.setRange(1, 16)
            self.query_workers_spin.setValue(4)
            self.parallel_profile_combo.addItem("safe", "safe")
            self.parallel_profile_combo.addItem("balanced", "balanced")
            self.parallel_profile_combo.addItem("aggressive", "aggressive")
            self.parallel_hint_label.setWordWrap(True)
            self.parallel_profile_combo.setToolTip(
                "Preset controls per-site pacing. Choosing a preset also resets album/query workers to that preset's defaults."
            )
            self.album_workers_spin.setToolTip(
                "How many albums run at once. You can change this after picking a preset to keep the preset pacing but use custom worker counts."
            )
            self.query_workers_spin.setToolTip(
                "How many source searches/details run at once per album. Manual edits override the preset worker defaults until you pick another preset."
            )
            self.parallel_hint_label.setToolTip(
                "Shows the current pacing preset and whether the worker counts still match that preset."
            )
            for spin in (self.auto_score_spin, self.review_score_spin):
                spin.setRange(0.0, 1.0)
                spin.setDecimals(2)
                spin.setSingleStep(0.05)
            self.auto_score_spin.setValue(0.85)
            self.review_score_spin.setValue(0.65)
            self._apply_parallel_profile("balanced")

            self.results_table.setHorizontalHeaderLabels(["Status", "Verified", "Approved", "Album", "Candidate", "Score", "Reason"])
            self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.results_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

            self.album_table.setHorizontalHeaderLabels(["Folder", "Search Name", "Tracks", "Priority URLs", "Manual Only"])
            self.album_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.album_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.album_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.album_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)

            self.tracks_table.setHorizontalHeaderLabels(["#", "Title", "Artist", "Source"])
            self.tracks_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.tracks_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

            self.evidence_table.setHorizontalHeaderLabels(["Source", "Title", "URL"])
            self.evidence_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.evidence_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.evidence_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.evidence_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

            self.history_table.setHorizontalHeaderLabels(["When", "Mode", "Root", "Report", "Summary"])
            self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

            self.approved_action_combo.addItem("(auto)", None)
            self.approved_action_combo.addItem("apply", DecisionAction.APPLY.value)
            self.approved_action_combo.addItem("skip", DecisionAction.SKIP.value)

        def _build_settings_group(self) -> QGroupBox:
            group = QGroupBox("Run Settings")
            layout = QVBoxLayout(group)

            path_form = QFormLayout()
            path_form.addRow("Music directory", self._with_browse(self.root_edit, self._choose_root_directory))
            path_form.addRow("Review report", self._with_browse(self.report_edit, self._choose_report_file))
            path_form.addRow("Apply output", self._with_browse(self.apply_output_edit, self._choose_apply_output_file))
            layout.addLayout(path_form)

            options_row = QHBoxLayout()
            source_group = QGroupBox("Sources")
            source_layout = QVBoxLayout(source_group)
            for check in (self.dizzylab_check, self.vocadb_check, self.vcpedia_check):
                check.setChecked(True)
                source_layout.addWidget(check)
            options_row.addWidget(source_group)

            matching_group = QGroupBox("Matching")
            matching_form = QFormLayout(matching_group)
            matching_form.addRow("Parallel profile", self.parallel_profile_combo)
            matching_form.addRow("Max candidates", self.max_candidates_spin)
            matching_form.addRow("Album workers", self.album_workers_spin)
            matching_form.addRow("Query workers", self.query_workers_spin)
            matching_form.addRow("Ready threshold", self.auto_score_spin)
            matching_form.addRow("Review threshold", self.review_score_spin)
            matching_form.addRow("Rate-limit hint", self.parallel_hint_label)
            options_row.addWidget(matching_group)

            llm_group = QGroupBox("LLM")
            llm_form = QFormLayout(llm_group)
            llm_form.addRow(self.llm_enabled_check)
            llm_form.addRow("Base URL", self.llm_base_edit)
            llm_form.addRow("API key", self.llm_key_edit)
            llm_form.addRow("Model", self.llm_model_edit)
            options_row.addWidget(llm_group, stretch=1)

            layout.addLayout(options_row)
            return group

        def _build_actions_group(self) -> QGroupBox:
            group = QGroupBox("Actions")
            layout = QHBoxLayout(group)
            layout.addWidget(self.match_button)
            layout.addWidget(self.load_button)
            layout.addWidget(self.save_button)
            layout.addWidget(self.import_config_button)
            layout.addWidget(self.export_config_button)
            layout.addWidget(self.clear_vocadb_csv_button)
            layout.addWidget(self.apply_button)
            layout.addWidget(self.rematch_selected_button)
            layout.addWidget(self.prev_button)
            layout.addWidget(self.next_button)
            layout.addWidget(self.approve_selected_button)
            layout.addWidget(self.approve_ready_button)
            layout.addWidget(self.skip_selected_button)
            layout.addWidget(self.skip_not_found_button)
            layout.addStretch(1)
            return group

        def _build_results_splitter(self) -> QSplitter:
            splitter = QSplitter(Qt.Horizontal)

            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_tabs = QTabWidget()
            albums_widget = QWidget()
            albums_layout = QVBoxLayout(albums_widget)
            albums_layout.addWidget(QLabel("Scanned Album Folders"))
            albums_layout.addWidget(self.album_table)
            results_widget = QWidget()
            results_layout = QVBoxLayout(results_widget)
            results_layout.addWidget(QLabel("Match Results"))
            results_layout.addWidget(self.results_table)
            left_tabs.addTab(albums_widget, "Albums")
            left_tabs.addTab(results_widget, "Results")
            left_layout.addWidget(left_tabs)

            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.addWidget(self._build_manual_review_group())

            tabs = QTabWidget()
            tabs.addTab(self._wrap_widget(self.summary_text), "Summary")
            tabs.addTab(self._wrap_widget(self.tracks_table), "Tracks")
            tabs.addTab(self._build_evidence_tab(), "Evidence")
            tabs.addTab(self._wrap_widget(self.json_text), "JSON")
            tabs.addTab(self._wrap_widget(self.history_table), "History")
            right_layout.addWidget(tabs, stretch=1)

            splitter.addWidget(left)
            splitter.addWidget(right)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            return splitter

        def _build_manual_review_group(self) -> QGroupBox:
            group = QGroupBox("Manual Review")
            form = QFormLayout(group)
            form.addRow("Search album name", self.override_album_title_edit)
            form.addRow("Priority URLs", self.priority_urls_edit)
            form.addRow("Manual raw text", self.manual_text_edit)
            form.addRow(self.manual_urls_only_check)
            form.addRow(self.manual_verified_check)
            form.addRow("Approved action", self.approved_action_combo)
            form.addRow("Reviewer", self.reviewer_edit)
            form.addRow("Notes", self.manual_notes_edit)
            buttons = QWidget()
            buttons_layout = QHBoxLayout(buttons)
            buttons_layout.setContentsMargins(0, 0, 0, 0)
            buttons_layout.addWidget(self.open_priority_url_button)
            buttons_layout.addWidget(self.open_album_folder_button)
            buttons_layout.addStretch(1)
            form.addRow("Shortcuts", buttons)
            return group

        def _build_evidence_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.addWidget(self.evidence_table)
            splitter = QSplitter(Qt.Horizontal)
            splitter.addWidget(self.evidence_preview_text)
            splitter.addWidget(self.comparison_text)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
            layout.addWidget(splitter, stretch=1)
            return widget

        @staticmethod
        def _wrap_widget(widget: QWidget) -> QWidget:
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
            return container

        def _with_browse(self, line_edit: QLineEdit, callback) -> QWidget:
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            button = QPushButton("Browse")
            button.clicked.connect(callback)
            layout.addWidget(line_edit)
            layout.addWidget(button)
            return container

        def _wire_events(self) -> None:
            self.match_button.clicked.connect(self.run_match)
            self.load_button.clicked.connect(self.load_report_file)
            self.save_button.clicked.connect(self.save_current_report)
            self.import_config_button.clicked.connect(self.import_gui_config)
            self.export_config_button.clicked.connect(self.export_gui_config)
            self.clear_vocadb_csv_button.clicked.connect(self.clear_vocadb_csv_cache_files)
            self.apply_button.clicked.connect(self.apply_verified_changes)
            self.rematch_selected_button.clicked.connect(self.rematch_selected_result)
            self.prev_button.clicked.connect(self.select_previous_result)
            self.next_button.clicked.connect(self.select_next_result)
            self.approve_selected_button.clicked.connect(self.approve_selected_result)
            self.approve_ready_button.clicked.connect(self.approve_ready_results)
            self.skip_selected_button.clicked.connect(self.skip_selected_result)
            self.skip_not_found_button.clicked.connect(self.skip_not_found_results)
            self.root_edit.editingFinished.connect(self.load_album_inventory_from_root)
            self.parallel_profile_combo.currentIndexChanged.connect(self.on_parallel_profile_changed)
            self.album_workers_spin.valueChanged.connect(self.on_parallel_workers_changed)
            self.query_workers_spin.valueChanged.connect(self.on_parallel_workers_changed)
            self.results_table.itemSelectionChanged.connect(self.on_result_selection_changed)
            self.album_table.itemSelectionChanged.connect(self.on_album_selection_changed)
            self.album_table.itemChanged.connect(self.on_album_table_item_changed)
            self.evidence_table.itemSelectionChanged.connect(self.on_evidence_selection_changed)
            self.history_table.itemDoubleClicked.connect(self.on_history_item_activated)
            self.manual_urls_only_check.toggled.connect(self.on_search_override_changed)
            self.manual_verified_check.toggled.connect(self.on_manual_review_changed)
            self.approved_action_combo.currentIndexChanged.connect(self.on_manual_review_changed)
            self.override_album_title_edit.textChanged.connect(self.on_search_override_changed)
            self.priority_urls_edit.textChanged.connect(self.on_search_override_changed)
            self.manual_text_edit.textChanged.connect(self.on_search_override_changed)
            self.open_priority_url_button.clicked.connect(self.open_priority_url)
            self.open_album_folder_button.clicked.connect(self.open_album_folder)
            self.reviewer_edit.textChanged.connect(self.on_manual_review_changed)
            self.manual_notes_edit.textChanged.connect(self.on_manual_review_changed)

        def _restore_state(self) -> None:
            self.root_edit.setText(self.settings.value("root_dir", self.root_edit.text()))
            self.report_edit.setText(self.settings.value("report_path", self.report_edit.text()))
            self.apply_output_edit.setText(self.settings.value("apply_output_path", self.apply_output_edit.text()))
            self.dizzylab_check.setChecked(self.settings.value("source_dizzylab", True, type=bool))
            self.vocadb_check.setChecked(self.settings.value("source_vocadb", True, type=bool))
            self.vcpedia_check.setChecked(self.settings.value("source_vcpedia", True, type=bool))
            self.max_candidates_spin.setValue(self.settings.value("max_candidates", 5, type=int))
            self.album_workers_spin.setValue(self.settings.value("album_workers", 4, type=int))
            self.query_workers_spin.setValue(self.settings.value("query_workers", 4, type=int))
            profile = self._set_parallel_profile_selection(self.settings.value("parallel_profile", "balanced"))
            self._apply_parallel_profile(profile, update_spins=False)
            self.auto_score_spin.setValue(self.settings.value("auto_score", 0.85, type=float))
            self.review_score_spin.setValue(self.settings.value("review_score", 0.65, type=float))
            self.llm_enabled_check.setChecked(self.settings.value("llm_enabled", True, type=bool))
            self.llm_base_edit.setText(self.settings.value("llm_base_url", ""))
            self.llm_key_edit.setText(self.settings.value("llm_api_key", ""))
            self.llm_model_edit.setText(self.settings.value("llm_model", ""))
            self._refresh_history_table()
            self.load_album_inventory_from_root()

        def closeEvent(self, event) -> None:  # pragma: no cover - GUI lifecycle
            self.settings.setValue("root_dir", self.root_edit.text())
            self.settings.setValue("report_path", self.report_edit.text())
            self.settings.setValue("apply_output_path", self.apply_output_edit.text())
            self.settings.setValue("source_dizzylab", self.dizzylab_check.isChecked())
            self.settings.setValue("source_vocadb", self.vocadb_check.isChecked())
            self.settings.setValue("source_vcpedia", self.vcpedia_check.isChecked())
            self.settings.setValue("max_candidates", self.max_candidates_spin.value())
            self.settings.setValue("album_workers", self.album_workers_spin.value())
            self.settings.setValue("query_workers", self.query_workers_spin.value())
            self.settings.setValue("parallel_profile", self.parallel_profile_combo.currentData() or "balanced")
            self.settings.setValue("auto_score", self.auto_score_spin.value())
            self.settings.setValue("review_score", self.review_score_spin.value())
            self.settings.setValue("llm_enabled", self.llm_enabled_check.isChecked())
            self.settings.setValue("llm_base_url", self.llm_base_edit.text())
            self.settings.setValue("llm_api_key", self.llm_key_edit.text())
            self.settings.setValue("llm_model", self.llm_model_edit.text())
            super().closeEvent(event)

        def import_gui_config(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Import GUI config", str(Path.cwd()), "JSON Files (*.json)")
            if not path:
                return
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception as exc:
                QMessageBox.critical(self, "Import failed", str(exc))
                return
            self._apply_gui_config_payload(payload)
            self.statusBar().showMessage(f"Imported GUI config from {path}", 5000)

        def export_gui_config(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Export GUI config", str(Path.cwd() / "musiclaw.gui.json"), "JSON Files (*.json)")
            if not path:
                return
            payload = self._build_gui_config_payload()
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"Exported GUI config to {path}", 5000)

        def clear_vocadb_csv_cache_files(self) -> None:
            deleted = clear_vocadb_csv_cache()
            self.statusBar().showMessage(f"Cleared {deleted} VocaDB CSV cache file(s)", 5000)

        def _build_gui_config_payload(self) -> dict:
            config = self.build_config().model_dump(mode="json")
            return {
                "root_dir": self.root_edit.text().strip(),
                "report_path": self.report_edit.text().strip(),
                "apply_output_path": self.apply_output_edit.text().strip(),
                "llm_model": self.llm_model_edit.text().strip(),
                "llm_api_key": self.llm_key_edit.text(),
                "config": config,
            }

        def _apply_gui_config_payload(self, payload: dict) -> None:
            config = payload.get("config", {})
            root = config.get("root", {})
            sources = config.get("sources", {})
            matching = config.get("matching", {})
            llm = config.get("llm", {})

            self.root_edit.setText(payload.get("root_dir") or root.get("music_dir") or self.root_edit.text())
            self.report_edit.setText(payload.get("report_path") or self.report_edit.text())
            self.apply_output_edit.setText(payload.get("apply_output_path") or self.apply_output_edit.text())
            enabled = set(sources.get("enabled", []))
            if enabled:
                self.dizzylab_check.setChecked("dizzylab" in enabled)
                self.vocadb_check.setChecked("vocadb" in enabled)
                self.vcpedia_check.setChecked("vcpedia" in enabled)
            self.max_candidates_spin.setValue(int(sources.get("max_candidates", self.max_candidates_spin.value())))
            processing = config.get("processing", {})
            self.album_workers_spin.setValue(int(processing.get("album_workers", self.album_workers_spin.value())))
            self.query_workers_spin.setValue(int(processing.get("query_workers", self.query_workers_spin.value())))
            profile = self._set_parallel_profile_selection(
                str(processing.get("parallel_profile", self.parallel_profile_combo.currentData() or "balanced"))
            )
            self._apply_parallel_profile(profile, update_spins=False)
            self.auto_score_spin.setValue(float(matching.get("auto_apply_score", self.auto_score_spin.value())))
            self.review_score_spin.setValue(float(matching.get("review_score", self.review_score_spin.value())))
            self.llm_enabled_check.setChecked(bool(llm.get("enabled", self.llm_enabled_check.isChecked())))
            self.llm_base_edit.setText(str(llm.get("base_url", payload.get("llm_base_url", self.llm_base_edit.text()))))
            self.llm_key_edit.setText(str(payload.get("llm_api_key", self.llm_key_edit.text())))
            self.llm_model_edit.setText(str(payload.get("llm_model", os.getenv("MUSICLAW_LLM_MODEL", self.llm_model_edit.text()))))

        def _load_recent_history(self) -> list[dict]:
            raw = self.settings.value("recent_tasks", "[]")
            try:
                history = json.loads(raw)
            except Exception:
                history = []
            return history if isinstance(history, list) else []

        def _save_recent_history(self, history: list[dict]) -> None:
            self.settings.setValue("recent_tasks", json.dumps(history, ensure_ascii=False))

        def _append_history_entry(self, report: RunReport, path: Path) -> None:
            history = self._load_recent_history()
            history.insert(
                0,
                {
                    "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "mode": report.mode,
                    "root": str(report.root),
                    "report": str(path),
                    "summary": render_console_summary(report),
                },
            )
            self._save_recent_history(history[:20])
            self._refresh_history_table()

        def _refresh_history_table(self) -> None:
            history = self._load_recent_history()
            self.history_table.setRowCount(len(history))
            for row, entry in enumerate(history):
                values = [entry.get("when", ""), entry.get("mode", ""), entry.get("root", ""), entry.get("report", ""), entry.get("summary", "")]
                for column, value in enumerate(values):
                    self.history_table.setItem(row, column, QTableWidgetItem(value))

        def load_album_inventory_from_root(self) -> None:
            root_text = self.root_edit.text().strip()
            if not root_text:
                self.album_inventory = []
                self.album_overrides = {}
                self._refresh_album_table()
                return
            root = Path(root_text)
            if not root.exists() or not root.is_dir():
                self.album_inventory = []
                self.album_overrides = {}
                self._refresh_album_table()
                return
            existing_overrides = self.album_overrides
            self.album_inventory = scan_music_root(root)
            self.album_overrides = {}
            for album in self.album_inventory:
                key = self._album_key(album)
                self.album_overrides[key] = existing_overrides.get(key, SearchOverrides())
            self._refresh_album_table()
            self.statusBar().showMessage(f"Loaded {len(self.album_inventory)} album folder(s)", 5000)

        def _refresh_album_table(self) -> None:
            selected_row = self.current_album_row if 0 <= self.current_album_row < len(self.album_inventory) else 0
            self._updating_album_table = True
            self.album_table.setRowCount(len(self.album_inventory))
            for row, album in enumerate(self.album_inventory):
                self._populate_album_row(row, album)
            self._updating_album_table = False
            if self.album_inventory:
                self.album_table.selectRow(selected_row)
                self.current_album_row = selected_row
            else:
                self.current_album_row = -1

        def _populate_album_row(self, row: int, album: LocalAlbum) -> None:
            override = self._override_for_album(album)
            values = [
                album.folder_name,
                override.album_title or album.guessed_title or album.folder_name,
                str(album.track_count),
                str(len(override.priority_urls)),
                "yes" if override.manual_urls_only else "no",
            ]
            for column, value in enumerate(values):
                item = self.album_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    if column != 1:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if column in {2, 3, 4}:
                        item.setTextAlignment(Qt.AlignCenter)
                    self.album_table.setItem(row, column, item)
                item.setText(value)

        def _album_key(self, album: LocalAlbum) -> str:
            return str(album.folder_path.resolve())

        def _override_for_album(self, album: LocalAlbum) -> SearchOverrides:
            return self.album_overrides.setdefault(self._album_key(album), SearchOverrides())

        def on_parallel_profile_changed(self) -> None:
            self._apply_parallel_profile(str(self.parallel_profile_combo.currentData() or "balanced"))

        def on_parallel_workers_changed(self) -> None:
            self._refresh_parallel_hint(str(self.parallel_profile_combo.currentData() or "balanced"))

        def _set_parallel_profile_selection(self, profile: str) -> str:
            index = self.parallel_profile_combo.findData(profile)
            if index < 0:
                index = self.parallel_profile_combo.findData("balanced")
            self.parallel_profile_combo.blockSignals(True)
            self.parallel_profile_combo.setCurrentIndex(max(index, 0))
            self.parallel_profile_combo.blockSignals(False)
            return str(self.parallel_profile_combo.currentData() or "balanced")

        @staticmethod
        def _parallel_presets() -> dict[str, dict[str, object]]:
            return {
                "safe": {
                    "album_workers": 2,
                    "query_workers": 2,
                    "hint": "Safe: lower concurrency, gentler per-site pacing; best when sites are rate-limiting or unstable.",
                },
                "aggressive": {
                    "album_workers": 8,
                    "query_workers": 8,
                    "hint": "Aggressive: faster across large libraries, but more likely to trigger site throttling.",
                },
                "balanced": {
                    "album_workers": 4,
                    "query_workers": 4,
                    "hint": "Balanced: moderate concurrency with built-in per-site throttling; recommended default.",
                },
            }

        def _refresh_parallel_hint(self, profile: str) -> None:
            presets = self._parallel_presets()
            preset = presets.get((profile or "balanced").casefold(), presets["balanced"])
            if (
                self.album_workers_spin.value() == int(preset["album_workers"])
                and self.query_workers_spin.value() == int(preset["query_workers"])
            ):
                self.parallel_hint_label.setText(
                    f"{preset['hint']} Choosing another profile resets both worker counts to that preset."
                )
                return
            self.parallel_hint_label.setText(
                " ".join(
                    [
                        f"{preset['hint']}",
                        f"Custom workers active: albums={self.album_workers_spin.value()}, queries={self.query_workers_spin.value()}.",
                        "The selected profile still controls per-site pacing; choosing another profile resets both worker counts.",
                    ]
                )
            )

        def _apply_parallel_profile(self, profile: str, *, update_spins: bool = True) -> None:
            profile_key = (profile or "balanced").casefold()
            presets = self._parallel_presets()
            preset = presets.get(profile_key, presets["balanced"])
            if update_spins:
                self.album_workers_spin.setValue(preset["album_workers"])
                self.query_workers_spin.setValue(preset["query_workers"])
            self._refresh_parallel_hint(profile_key)

        def _choose_root_directory(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, "Select music directory", self.root_edit.text() or str(Path.cwd()))
            if directory:
                self.root_edit.setText(directory)
                self.load_album_inventory_from_root()

        def _choose_report_file(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Select review report path", self.report_edit.text() or "reports/latest.json", "JSON Files (*.json)")
            if path:
                self.report_edit.setText(path)

        def _choose_apply_output_file(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Select apply output path", self.apply_output_edit.text() or "reports/apply.json", "JSON Files (*.json)")
            if path:
                self.apply_output_edit.setText(path)

        def build_config(self) -> AppConfig:
            enabled = []
            if self.dizzylab_check.isChecked():
                enabled.append("dizzylab")
            if self.vocadb_check.isChecked():
                enabled.append("vocadb")
            if self.vcpedia_check.isChecked():
                enabled.append("vcpedia")
            if not enabled:
                enabled = ["dizzylab"]

            self._set_env("MUSICLAW_LLM_BASE_URL", self.llm_base_edit.text())
            self._set_env("MUSICLAW_LLM_API_KEY", self.llm_key_edit.text())
            self._set_env("MUSICLAW_LLM_MODEL", self.llm_model_edit.text())

            return AppConfig(
                root=RootConfig(music_dir=Path(self.root_edit.text() or ".")),
                sources=SourcesConfig(enabled=enabled, max_candidates=self.max_candidates_spin.value()),
                matching=MatchingConfig(auto_apply_score=self.auto_score_spin.value(), review_score=self.review_score_spin.value()),
                llm=LlmConfig(enabled=self.llm_enabled_check.isChecked(), base_url=self.llm_base_edit.text().strip()),
                network=NetworkConfig(),
                processing=ProcessingConfig(
                    album_workers=self.album_workers_spin.value(),
                    query_workers=self.query_workers_spin.value(),
                    parallel_profile=str(self.parallel_profile_combo.currentData() or "balanced"),
                ),
                tags=TagsConfig(),
                cache=CacheConfig(dir=Path("cache")),
            )

        @staticmethod
        def _set_env(key: str, value: str) -> None:
            if value.strip():
                os.environ[key] = value.strip()
            elif key in os.environ:
                os.environ.pop(key, None)

        def run_match(self) -> None:
            root = Path(self.root_edit.text().strip())
            if not root.exists() or not root.is_dir():
                QMessageBox.warning(self, "Invalid directory", "Please select a valid music directory.")
                return
            if not self.album_inventory:
                self.load_album_inventory_from_root()
            if not self.album_inventory:
                QMessageBox.information(self, "No albums found", "No album folders with audio files were found in the selected directory.")
                return
            album_inputs = [(album.model_copy(deep=True), self._override_for_album(album).model_copy(deep=True)) for album in self.album_inventory]
            self._start_worker(mode="match-batch", config=self.build_config(), root=root, album_inputs=album_inputs)

        def load_report_file(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Open review report", self.report_edit.text() or "reports/latest.json", "JSON Files (*.json)")
            if not path:
                return
            self.report_edit.setText(path)
            try:
                report = load_report(Path(path))
            except Exception as exc:
                QMessageBox.critical(self, "Load failed", str(exc))
                return
            self.load_report(report)

        def save_current_report(self) -> None:
            if self.current_report is None:
                QMessageBox.information(self, "Nothing to save", "Run a match or load a report first.")
                return
            path = Path(self.report_edit.text().strip() or "reports/latest.json")
            save_report(self.current_report, path)
            self.statusBar().showMessage(f"Saved report to {path}", 5000)

        def apply_verified_changes(self) -> None:
            if self.current_report is None:
                QMessageBox.information(self, "No report", "Run a match or load a report first.")
                return
            actionable = sum(
                1
                for result in self.current_report.results
                if result.plan.manual_review.verified
                and result.plan.candidate is not None
                and result.plan.tag_writes
                and result.plan.manual_review.approved_action != DecisionAction.SKIP
                and result.plan.status in {MatchStatus.READY, MatchStatus.REVIEW}
            )
            if actionable == 0:
                QMessageBox.information(self, "No actionable albums", "Mark at least one ready/review album as verified before applying changes.")
                return
            answer = QMessageBox.question(
                self,
                "Confirm apply",
                f"Apply approved metadata changes to {actionable} album(s)? This will modify files on disk.",
            )
            if answer != QMessageBox.Yes:
                return
            self._start_worker(mode="apply", config=self.build_config(), report=self.current_report.model_copy(deep=True))

        def _start_worker(
            self,
            mode: str,
            config: AppConfig,
            root: Path | None = None,
            report: RunReport | None = None,
            album: LocalAlbum | None = None,
            overrides: SearchOverrides | None = None,
            album_inputs: list[tuple[LocalAlbum, SearchOverrides]] | None = None,
        ) -> None:
            if self._worker_thread is not None:
                QMessageBox.information(self, "Busy", "A task is already running.")
                return
            self.set_controls_enabled(False)
            self.statusBar().showMessage(f"Running {mode}...")

            self._worker_thread = QThread(self)
            self._worker = PipelineWorker(
                mode=mode,
                config=config,
                root=root,
                report=report,
                album=album,
                overrides=overrides,
                album_inputs=album_inputs,
            )
            self._worker.moveToThread(self._worker_thread)
            self._worker_thread.started.connect(self._worker.run)
            self._worker.finished.connect(self._on_worker_finished)
            self._worker.failed.connect(self._on_worker_failed)
            self._worker.finished.connect(self._worker_thread.quit)
            self._worker.failed.connect(self._worker_thread.quit)
            self._worker_thread.finished.connect(self._cleanup_worker)
            self._worker_thread.start()

        def _on_worker_finished(self, payload) -> None:
            if isinstance(payload, AlbumProcessingResult):
                album_key = self._album_key(payload.album)
                self.album_overrides[album_key] = payload.plan.search_overrides.model_copy(deep=True)
                self._refresh_album_table()
                if self.current_report is None:
                    self.current_report = RunReport(root=payload.album.folder_path.parent, processed_at=datetime.now(timezone.utc).isoformat(), mode="match", results=[])
                result_row = self._find_result_row_for_album(payload.album)
                if result_row is None:
                    self.current_report.results.append(payload)
                    result_row = len(self.current_report.results) - 1
                    self.results_table.setRowCount(len(self.current_report.results))
                else:
                    self.current_report.results[result_row] = payload
                save_report(self.current_report, Path(self.report_edit.text().strip() or "reports/latest.json"))
                self._fill_result_row(result_row, payload)
                self.results_table.selectRow(result_row)
                self.meta_label.setText(render_console_summary(self.current_report))
                self.statusBar().showMessage(f"Re-matched {payload.album.folder_name}", 10000)
                return

            report = payload
            if report.mode == "match":
                path = Path(self.report_edit.text().strip() or "reports/latest.json")
                save_report(report, path)
            else:
                path = Path(self.apply_output_edit.text().strip() or "reports/apply.json")
                save_report(report, path)
            self._append_history_entry(report, path)
            self.load_report(report)
            self.statusBar().showMessage(render_console_summary(report), 10000)

        def _on_worker_failed(self, error_text: str) -> None:
            QMessageBox.critical(self, "Task failed", error_text)
            self.statusBar().showMessage("Task failed", 10000)

        def _cleanup_worker(self) -> None:
            self.set_controls_enabled(True)
            if self._worker_thread is not None:
                self._worker_thread.deleteLater()
            self._worker_thread = None
            self._worker = None

        def set_controls_enabled(self, enabled: bool) -> None:
            for widget in (
                self.match_button,
                self.load_button,
                self.save_button,
                self.apply_button,
                self.root_edit,
                self.report_edit,
                self.apply_output_edit,
                self.dizzylab_check,
                self.vocadb_check,
                self.vcpedia_check,
                self.max_candidates_spin,
                self.album_workers_spin,
                self.query_workers_spin,
                self.parallel_profile_combo,
                self.auto_score_spin,
                self.review_score_spin,
                self.llm_enabled_check,
                self.llm_base_edit,
                self.llm_key_edit,
                self.llm_model_edit,
                self.override_album_title_edit,
                self.priority_urls_edit,
                self.manual_text_edit,
                self.import_config_button,
                self.export_config_button,
                self.clear_vocadb_csv_button,
                self.rematch_selected_button,
                self.album_table,
                self.prev_button,
                self.next_button,
                self.approve_selected_button,
                self.approve_ready_button,
                self.skip_selected_button,
                self.skip_not_found_button,
            ):
                widget.setEnabled(enabled)

        def load_report(self, report: RunReport) -> None:
            self.current_report = report
            self.album_inventory = [result.album.model_copy(deep=True) for result in report.results]
            self.album_overrides = {
                self._album_key(result.album): result.plan.search_overrides.model_copy(deep=True)
                for result in report.results
            }
            self._refresh_album_table()
            self.meta_label.setText(render_console_summary(report))
            self.results_table.setRowCount(len(report.results))
            for row, result in enumerate(report.results):
                self._fill_result_row(row, result)
            if report.results:
                self.results_table.selectRow(0)
            else:
                self.current_row = -1
                self.summary_text.clear()
                self.json_text.clear()
                self.tracks_table.setRowCount(0)
                self.evidence_table.setRowCount(0)
                self.evidence_preview_text.clear()
                self.comparison_text.clear()

        def _fill_result_row(self, row: int, result: AlbumProcessingResult) -> None:
            score = result.plan.breakdown.total if result.plan.candidate else 0.0
            candidate = result.plan.candidate.title if result.plan.candidate and result.plan.candidate.title else "-"
            approved = result.plan.manual_review.approved_action.value if result.plan.manual_review.approved_action else "(auto)"
            values = [
                result.plan.status.value,
                "yes" if result.plan.manual_review.verified else "no",
                approved,
                result.album.folder_name,
                candidate,
                f"{score:.2f}",
                result.plan.reason or "-",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 1, 2}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.results_table.setItem(row, column, item)

        def on_album_selection_changed(self) -> None:
            selected = self.album_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            if row < 0 or row >= len(self.album_inventory):
                return
            self.current_album_row = row
            album = self.album_inventory[row]
            if self.current_report is not None:
                matching_row = self._find_result_row_for_album(album)
                if matching_row is not None and matching_row != self.current_row:
                    self.results_table.selectRow(matching_row)
                    return
            self._load_album_override_details(album)

        def on_album_table_item_changed(self, item) -> None:
            if self._updating_album_table:
                return
            row = item.row()
            column = item.column()
            if row < 0 or row >= len(self.album_inventory) or column != 1:
                return
            album = self.album_inventory[row]
            override = self._override_for_album(album)
            override.album_title = item.text().strip() or None
            if self.current_album_row == row:
                self._load_album_override_details(album)
            if self.current_report is not None:
                matching_row = self._find_result_row_for_album(album)
                if matching_row is not None:
                    self.current_report.results[matching_row].plan.search_overrides.album_title = override.album_title

        def _load_album_override_details(self, album: LocalAlbum) -> None:
            override = self._override_for_album(album)
            self.override_album_title_edit.blockSignals(True)
            self.priority_urls_edit.blockSignals(True)
            self.manual_text_edit.blockSignals(True)
            self.manual_urls_only_check.blockSignals(True)
            self.override_album_title_edit.setText(override.album_title or album.guessed_title or album.folder_name)
            self.priority_urls_edit.setPlainText("\n".join(override.priority_urls))
            self.manual_text_edit.setPlainText(override.manual_text or "")
            self.manual_urls_only_check.setChecked(override.manual_urls_only)
            self.override_album_title_edit.blockSignals(False)
            self.priority_urls_edit.blockSignals(False)
            self.manual_text_edit.blockSignals(False)
            self.manual_urls_only_check.blockSignals(False)
            self.summary_text.setPlainText(self._build_album_override_summary(album, override))

        def _build_album_override_summary(self, album: LocalAlbum, override: SearchOverrides) -> str:
            return "\n".join(
                [
                    f"Album folder: {album.folder_name}",
                    f"Search album name: {override.album_title or album.guessed_title or album.folder_name}",
                    f"Tracks: {album.track_count}",
                    f"Priority URLs: {', '.join(override.priority_urls) or '-'}",
                    f"Manual text: {self._manual_text_summary(override.manual_text)}",
                    f"Manual URL only: {'yes' if override.manual_urls_only else 'no'}",
                ]
            )

        def _find_result_row_for_album(self, album: LocalAlbum) -> int | None:
            if self.current_report is None:
                return None
            album_key = self._album_key(album)
            for index, result in enumerate(self.current_report.results):
                if self._album_key(result.album) == album_key:
                    return index
            return None

        def on_result_selection_changed(self) -> None:
            if self.current_report is None:
                return
            selected = self.results_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            self.current_row = row
            result = self.current_report.results[row]
            self._select_album_row_for_result(result)
            self._load_result_details(result)

        def _select_album_row_for_result(self, result: AlbumProcessingResult) -> None:
            album_key = self._album_key(result.album)
            for row, album in enumerate(self.album_inventory):
                if self._album_key(album) == album_key:
                    self.album_table.blockSignals(True)
                    self.album_table.selectRow(row)
                    self.album_table.blockSignals(False)
                    self.current_album_row = row
                    break

        def select_previous_result(self) -> None:
            if self.current_report is None or not self.current_report.results:
                return
            row = max(0, (self.current_row if self.current_row >= 0 else 0) - 1)
            self.results_table.selectRow(row)

        def select_next_result(self) -> None:
            if self.current_report is None or not self.current_report.results:
                return
            row = min(len(self.current_report.results) - 1, (self.current_row if self.current_row >= 0 else -1) + 1)
            self.results_table.selectRow(row)

        def approve_selected_result(self) -> None:
            if self.current_report is None or self.current_row < 0:
                return
            result = self.current_report.results[self.current_row]
            result.plan.manual_review.verified = True
            result.plan.manual_review.approved_action = DecisionAction.APPLY if result.plan.status == MatchStatus.REVIEW else None
            self._fill_result_row(self.current_row, result)
            self._load_result_details(result)
            self.meta_label.setText(render_console_summary(self.current_report))

        def approve_ready_results(self) -> None:
            if self.current_report is None:
                return
            changed = 0
            for row, result in enumerate(self.current_report.results):
                if result.plan.status == MatchStatus.READY:
                    result.plan.manual_review.verified = True
                    if result.plan.manual_review.approved_action == DecisionAction.SKIP:
                        result.plan.manual_review.approved_action = None
                    self._fill_result_row(row, result)
                    changed += 1
            self.meta_label.setText(render_console_summary(self.current_report))
            self.statusBar().showMessage(f"Approved {changed} ready album(s)", 5000)
            if self.current_row >= 0 and self.current_row < len(self.current_report.results):
                self._load_result_details(self.current_report.results[self.current_row])

        def skip_selected_result(self) -> None:
            if self.current_report is None or self.current_row < 0:
                return
            result = self.current_report.results[self.current_row]
            result.plan.manual_review.verified = True
            result.plan.manual_review.approved_action = DecisionAction.SKIP
            self._fill_result_row(self.current_row, result)
            self._load_result_details(result)
            self.meta_label.setText(render_console_summary(self.current_report))

        def skip_not_found_results(self) -> None:
            if self.current_report is None:
                return
            changed = 0
            for row, result in enumerate(self.current_report.results):
                if result.plan.status == MatchStatus.NOT_FOUND:
                    result.plan.manual_review.verified = True
                    result.plan.manual_review.approved_action = DecisionAction.SKIP
                    self._fill_result_row(row, result)
                    changed += 1
            self.meta_label.setText(render_console_summary(self.current_report))
            self.statusBar().showMessage(f"Marked {changed} not_found album(s) as skip", 5000)
            if self.current_row >= 0 and self.current_row < len(self.current_report.results):
                self._load_result_details(self.current_report.results[self.current_row])

        def _load_result_details(self, result: AlbumProcessingResult) -> None:
            review = result.plan.manual_review
            overrides = result.plan.search_overrides
            self.manual_verified_check.blockSignals(True)
            self.approved_action_combo.blockSignals(True)
            self.override_album_title_edit.blockSignals(True)
            self.priority_urls_edit.blockSignals(True)
            self.manual_text_edit.blockSignals(True)
            self.manual_urls_only_check.blockSignals(True)
            self.reviewer_edit.blockSignals(True)
            self.manual_notes_edit.blockSignals(True)

            self.override_album_title_edit.setText(overrides.album_title or "")
            self.priority_urls_edit.setPlainText("\n".join(overrides.priority_urls))
            self.manual_text_edit.setPlainText(overrides.manual_text or "")
            self.manual_urls_only_check.setChecked(overrides.manual_urls_only)
            self.manual_verified_check.setChecked(review.verified)
            index = self.approved_action_combo.findData(review.approved_action.value if review.approved_action else None)
            self.approved_action_combo.setCurrentIndex(max(index, 0))
            self.reviewer_edit.setText(review.reviewer or "")
            self.manual_notes_edit.setPlainText(review.notes or "")

            self.manual_verified_check.blockSignals(False)
            self.approved_action_combo.blockSignals(False)
            self.override_album_title_edit.blockSignals(False)
            self.priority_urls_edit.blockSignals(False)
            self.manual_text_edit.blockSignals(False)
            self.manual_urls_only_check.blockSignals(False)
            self.reviewer_edit.blockSignals(False)
            self.manual_notes_edit.blockSignals(False)

            self.summary_text.setPlainText(self._build_summary_text(result))
            self.json_text.setPlainText(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
            self._populate_tracks_table(result)
            self._populate_evidence_table(result)
            self.comparison_text.setPlainText(self._build_comparison_text(result))

        def _build_summary_text(self, result: AlbumProcessingResult) -> str:
            candidate = result.plan.candidate
            overrides = result.plan.search_overrides
            lines = [
                f"Album: {result.album.folder_name}",
                f"Search album name: {overrides.album_title or result.album.guessed_title or result.album.folder_name}",
                f"Status: {result.plan.status.value}",
                f"Suggested action: {result.plan.action.value}",
                f"Reason: {result.plan.reason or '-'}",
                f"Score: {result.plan.breakdown.total:.2f}",
                f"Verified: {'yes' if result.plan.manual_review.verified else 'no'}",
                f"Approved action: {result.plan.manual_review.approved_action.value if result.plan.manual_review.approved_action else '(auto)'}",
                "",
                f"Candidate title: {candidate.title if candidate else '-'}",
                f"Circle: {candidate.circle if candidate else '-'}",
                f"Catalog: {candidate.catalog_no if candidate else '-'}",
                f"Release date: {candidate.release_date if candidate else '-'}",
                f"Event: {candidate.event_name if candidate else '-'}",
                f"Confidence: {candidate.confidence if candidate else 0.0}",
                "",
                "Collection summary:",
                f"- Queries: {', '.join(result.plan.collection_summary.queries) or '-'}",
                f"- Sources: {', '.join(source.value for source in result.plan.collection_summary.searched_sources) or '-'}",
                f"- Candidates: {result.plan.collection_summary.candidate_count}",
                f"- Evidence pages: {result.plan.collection_summary.evidence_count}",
                f"- Priority URLs: {', '.join(overrides.priority_urls) or '-'}",
                f"- Manual text: {self._manual_text_summary(overrides.manual_text)}",
            ]
            if result.plan.collection_summary.errors:
                lines.append("- Collection errors:")
                lines.extend(f"  * {error}" for error in result.plan.collection_summary.errors)
            if candidate and candidate.conflicts:
                lines.append("")
                lines.append("Candidate conflicts:")
                lines.extend(f"- {conflict}" for conflict in candidate.conflicts)
            if result.plan.evidence_pages:
                lines.append("")
                lines.append("Evidence pages:")
                for page in result.plan.evidence_pages:
                    lines.append(f"- [{page.source.value}] {page.url}")
                    if page.notes:
                        lines.extend(f"  * {note}" for note in page.notes)
            return "\n".join(lines)

        def _populate_tracks_table(self, result: AlbumProcessingResult) -> None:
            tracks = result.plan.candidate.tracks if result.plan.candidate else []
            self.tracks_table.setRowCount(len(tracks))
            for row, track in enumerate(tracks):
                self.tracks_table.setItem(row, 0, QTableWidgetItem(str(track.number)))
                self.tracks_table.setItem(row, 1, QTableWidgetItem(track.title))
                self.tracks_table.setItem(row, 2, QTableWidgetItem(self._preview_track_artist(result, track.number, track.title, track.artist)))
                self.tracks_table.setItem(row, 3, QTableWidgetItem(self._preview_track_source(result, track.number, track.title, track.evidence_url)))

        @staticmethod
        def _matching_evidence_track(result: AlbumProcessingResult, track_number: int, track_title: str):
            normalized_title = (track_title or "").strip().casefold()
            for page in result.plan.evidence_pages:
                for page_track in page.tracks:
                    if page_track.number != track_number:
                        continue
                    if normalized_title and page_track.title and page_track.title.strip().casefold() != normalized_title:
                        continue
                    return page_track, page.url
            return None, None

        def _preview_track_artist(self, result: AlbumProcessingResult, track_number: int, track_title: str, artist: str | None) -> str:
            if artist:
                return artist
            page_track, _page_url = self._matching_evidence_track(result, track_number, track_title)
            return page_track.artist if page_track and page_track.artist else ""

        def _preview_track_source(self, result: AlbumProcessingResult, track_number: int, track_title: str, source_url: str | None) -> str:
            if source_url:
                return source_url
            page_track, page_url = self._matching_evidence_track(result, track_number, track_title)
            if page_track and page_track.source_url:
                return page_track.source_url
            return page_url or ""

        def _populate_evidence_table(self, result: AlbumProcessingResult) -> None:
            pages = result.plan.evidence_pages
            self.evidence_table.setRowCount(len(pages))
            for row, page in enumerate(pages):
                self.evidence_table.setItem(row, 0, QTableWidgetItem(page.source.value))
                title = page.title.value if page.title and page.title.value else "-"
                self.evidence_table.setItem(row, 1, QTableWidgetItem(title))
                self.evidence_table.setItem(row, 2, QTableWidgetItem(page.url))
            if pages:
                self.evidence_table.selectRow(0)
            else:
                self.evidence_preview_text.clear()

        def on_evidence_selection_changed(self) -> None:
            if self.current_report is None or self.current_row < 0:
                return
            selected = self.evidence_table.selectedItems()
            if not selected:
                return
            evidence_row = selected[0].row()
            pages = self.current_report.results[self.current_row].plan.evidence_pages
            if evidence_row >= len(pages):
                return
            page = pages[evidence_row]
            self.evidence_preview_text.setPlainText(self._build_evidence_preview(page))

        def _build_evidence_preview(self, page) -> str:
            lines = [
                f"Source: {page.source.value}",
                f"URL: {page.url}",
                f"Title: {page.title.value if page.title else '-'}",
                f"Circle: {page.circle.value if page.circle else '-'}",
                f"Album artist: {page.album_artist.value if page.album_artist else '-'}",
                f"Catalog: {page.catalog_no.value if page.catalog_no else '-'}",
                f"Release date: {page.release_date.value if page.release_date else '-'}",
                f"Event: {page.event_name.value if page.event_name else '-'}",
                f"Cover: {page.cover_url.value if page.cover_url else '-'}",
                f"Tags: {', '.join(page.tags) or '-'}",
                "",
                "Notes:",
            ]
            lines.extend(page.notes or ["-"])
            lines.append("")
            lines.append("Raw payload:")
            lines.append(json.dumps(page.raw_payload, ensure_ascii=False, indent=2))
            return "\n".join(lines)

        def _build_comparison_text(self, result: AlbumProcessingResult) -> str:
            pages = result.plan.evidence_pages
            if not pages:
                return "No evidence pages."
            field_rows = [
                ("title", [page.title.value if page.title else None for page in pages]),
                ("circle", [page.circle.value if page.circle else None for page in pages]),
                ("album_artist", [page.album_artist.value if page.album_artist else None for page in pages]),
                ("catalog_no", [page.catalog_no.value if page.catalog_no else None for page in pages]),
                ("release_date", [page.release_date.value if page.release_date else None for page in pages]),
                ("event_name", [page.event_name.value if page.event_name else None for page in pages]),
                ("track_count", [str(len(page.tracks)) for page in pages]),
            ]
            lines = ["Source Comparison:"]
            for field_name, values in field_rows:
                normalized = {value for value in values if value}
                marker = "[OK]" if len(normalized) <= 1 else "[DIFF]"
                lines.append(f"{marker} {field_name}")
                for page, value in zip(pages, values):
                    lines.append(f"  - {page.source.value}: {value or '-'}")
            candidate = result.plan.candidate
            if candidate and candidate.conflicts:
                lines.append("")
                lines.append("Resolver conflicts:")
                lines.extend(f"- {conflict}" for conflict in candidate.conflicts)
            return "\n".join(lines)

        def on_history_item_activated(self, *_args) -> None:
            selected = self.history_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            history = self._load_recent_history()
            if row >= len(history):
                return
            entry = history[row]
            self.root_edit.setText(entry.get("root", self.root_edit.text()))
            self.report_edit.setText(entry.get("report", self.report_edit.text()))
            report_path = Path(entry.get("report", ""))
            if report_path.exists():
                try:
                    self.load_report(load_report(report_path))
                except Exception as exc:
                    QMessageBox.warning(self, "History item load failed", str(exc))

        def on_manual_review_changed(self) -> None:
            if self.current_report is None or self.current_row < 0:
                return
            result = self.current_report.results[self.current_row]
            result.plan.manual_review.verified = self.manual_verified_check.isChecked()
            approved_value = self.approved_action_combo.currentData()
            result.plan.manual_review.approved_action = DecisionAction(approved_value) if approved_value else None
            result.plan.manual_review.reviewer = self.reviewer_edit.text().strip() or None
            result.plan.manual_review.notes = self.manual_notes_edit.toPlainText().strip() or None
            self._fill_result_row(self.current_row, result)
            self.meta_label.setText(render_console_summary(self.current_report))

        def on_search_override_changed(self) -> None:
            if self.current_album_row < 0 or self.current_album_row >= len(self.album_inventory):
                return
            album = self.album_inventory[self.current_album_row]
            override = self._override_for_album(album)
            override.album_title = self.override_album_title_edit.text().strip() or None
            override.priority_urls = [
                line.strip()
                for line in self.priority_urls_edit.toPlainText().splitlines()
                if line.strip()
            ]
            override.manual_text = self.manual_text_edit.toPlainText().strip() or None
            override.manual_urls_only = self.manual_urls_only_check.isChecked()
            self._updating_album_table = True
            self._populate_album_row(self.current_album_row, album)
            self._updating_album_table = False
            if self.current_report is not None:
                matching_row = self._find_result_row_for_album(album)
                if matching_row is not None:
                    result = self.current_report.results[matching_row]
                    result.plan.search_overrides = override.model_copy(deep=True)
                    self.json_text.setPlainText(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
                    self.summary_text.setPlainText(self._build_summary_text(result))
            else:
                self.summary_text.setPlainText(self._build_album_override_summary(album, override))

        @staticmethod
        def _manual_text_summary(manual_text: str | None) -> str:
            if not manual_text:
                return "-"
            line_count = len([line for line in manual_text.splitlines() if line.strip()]) or 1
            return f"provided ({len(manual_text)} chars, {line_count} line(s))"

        def rematch_selected_result(self) -> None:
            if self.current_album_row < 0 or self.current_album_row >= len(self.album_inventory):
                QMessageBox.information(self, "No selection", "Select an album first.")
                return
            album = self.album_inventory[self.current_album_row]
            overrides = self._override_for_album(album)
            self._start_worker(
                mode="match-album",
                config=self.build_config(),
                album=album.model_copy(deep=True),
                overrides=overrides.model_copy(deep=True),
            )

        def open_priority_url(self) -> None:
            if self.current_album_row < 0 or self.current_album_row >= len(self.album_inventory):
                return
            urls = self._override_for_album(self.album_inventory[self.current_album_row]).priority_urls
            if not urls:
                QMessageBox.information(self, "No priority URL", "Enter at least one priority URL first.")
                return
            webbrowser.open(urls[0])

        def open_album_folder(self) -> None:
            if self.current_album_row < 0 or self.current_album_row >= len(self.album_inventory):
                return
            folder_path = self.album_inventory[self.current_album_row].folder_path
            if not folder_path.exists():
                QMessageBox.warning(self, "Folder not found", f"Album folder does not exist: {folder_path}")
                return
            if os.name == "nt":
                os.startfile(str(folder_path))
            else:
                webbrowser.open(folder_path.resolve().as_uri())


def main() -> int:
    if QT_API is None:  # pragma: no cover - depends on optional runtime dependency
        message = "PySide6 or PyQt5 is required for the GUI. Install with `pip install -e .[gui]` or `pip install PySide6`."
        if QT_IMPORT_ERROR is not None:
            message += f"\n\nImport error: {QT_IMPORT_ERROR}"
        raise SystemExit(message)

    app = QApplication(sys.argv)
    window = MusicLawMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch entrypoint
    raise SystemExit(main())
