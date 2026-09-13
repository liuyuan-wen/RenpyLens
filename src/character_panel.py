"""Editable character profiles inside the translation workbench."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QComboBox, QLabel, QTextEdit, QSplitter, QHeaderView,
)

from i18n import tr


class CharacterPanel(QWidget):
    save_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._game_id = ""
        self._profiles = {}
        self._items = {}
        self._loading = False
        self._columns_sized = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setColumnCount(4)
        self.list.header().setSectionResizeMode(QHeaderView.Interactive)
        self.list.header().setStretchLastSection(False)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for column, width in enumerate((110, 200, 110, 240)):
            self.list.setColumnWidth(column, width)
        splitter.addWidget(self.list)
        self.editor = QWidget()
        form = QVBoxLayout(self.editor)
        self.name = QLabel()
        self.name.setTextFormat(Qt.PlainText)
        self.gender_label = QLabel()
        self.gender = QComboBox()
        for value in ("unknown", "male", "female", "other"):
            self.gender.addItem("", value)
        self.notes_label = QLabel()
        self.notes = QTextEdit()
        self.notes.setAcceptRichText(False)
        for widget in (self.name, self.gender_label, self.gender, self.notes_label, self.notes):
            form.addWidget(widget)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        self.editor.setEnabled(False)
        self.list.currentItemChanged.connect(self._select)
        self.gender.currentIndexChanged.connect(self._save)
        self.notes.textChanged.connect(self._save)
        self.retranslate_ui()

    def showEvent(self, event):
        super().showEvent(event)
        self._size_initial_columns()

    def _size_initial_columns(self):
        # Size once after styling and population; subsequent refreshes keep user widths.
        if self._columns_sized or not self._items or not self.isVisible():
            return
        self.list.ensurePolished()
        for column in range(3):
            self.list.resizeColumnToContents(column)
            self.list.setColumnWidth(column, self.list.columnWidth(column) + 16)
        self._columns_sized = True

    def retranslate_ui(self):
        self.list.setHeaderLabels([tr("characters.variable"), tr("characters.name"),
                                   tr("characters.gender"), tr("characters.notes")])
        self.gender_label.setText(tr("characters.gender"))
        self.notes_label.setText(tr("characters.notes"))
        for i in range(self.gender.count()):
            self.gender.setItemText(i, tr("characters." + self.gender.itemData(i)))
        for key in self._items:
            self._update_row(key)

    def _update_row(self, key):
        profile = self._profiles[key]
        row = self._items[key]
        row.setText(0, "" if key.startswith("name:") else key)
        row.setText(1, profile["display_name"])
        row.setText(2, tr("characters." + profile["gender"]))
        row.setText(3, " ".join(profile["notes"].split()))

    def set_characters(self, game_id, profiles):
        # Update rows in place: predictions must not reset selection, cursor or drafts.
        self._loading = True
        try:
            if game_id != self._game_id:
                self.list.clear()
                self._items.clear()
                self._profiles.clear()
                self._game_id = game_id
                self.notes.clear()
                self.name.clear()
                self.gender.setCurrentIndex(0)
                self.editor.setEnabled(False)
            for profile in profiles:
                key = profile["character_id"]
                self._profiles[key] = dict(profile)
                if key not in self._items:
                    item = QTreeWidgetItem(self.list)
                    item.setData(0, Qt.UserRole, key)
                    self._items[key] = item
                self._update_row(key)
            selected = self.list.currentItem()
            if selected:
                self.name.setText(self._profiles[selected.data(0, Qt.UserRole)]["display_name"])
        finally:
            self._loading = False
        if not self.list.currentItem() and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self._size_initial_columns()

    def _select(self, item, previous):
        if self._loading:
            return
        self._loading = True
        try:
            self.editor.setEnabled(item is not None)
            if item:
                profile = self._profiles[item.data(0, Qt.UserRole)]
                self.name.setText(profile["display_name"])
                self.gender.setCurrentIndex(self.gender.findData(profile["gender"]))
                self.notes.setPlainText(profile["notes"])
        finally:
            self._loading = False

    def _save(self, *_):
        item = self.list.currentItem()
        if self._loading or item is None:
            return
        key = item.data(0, Qt.UserRole)
        profile = self._profiles[key]
        profile.update(gender=self.gender.currentData(), notes=self.notes.toPlainText())
        self._update_row(key)
        self.save_requested.emit({"game_id": self._game_id, **profile})
