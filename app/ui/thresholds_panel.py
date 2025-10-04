# app/ui/thresholds_panel.py
from PySide6.QtWidgets import QWidget, QFormLayout, QDoubleSpinBox, QSpinBox, QPushButton, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

class ThresholdsPanel(QWidget):
    """
    Jednoduchý editor prahov pre aktuálny recept.
    Očakáva objekt s atribútmi:
      - tool (ToolService) s .thresholds dict + .save_thresholds()
      - db (DbService), recipes (RecipeService)
      - current_recipe_name(): str
    """
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setContentsMargins(0,0,0,0)

        self.f = QFormLayout(self)
        self.ssim_min = QDoubleSpinBox(); self.ssim_min.setRange(0.0, 1.0); self.ssim_min.setSingleStep(0.01)
        self.diff_thr = QSpinBox(); self.diff_thr.setRange(0, 255)
        self.min_blob = QSpinBox(); self.min_blob.setRange(0, 100000)
        self.max_area = QSpinBox(); self.max_area.setRange(0, 10000000)
        self.max_cnt  = QSpinBox(); self.max_cnt.setRange(0, 100000)

        self.f.addRow("SSIM min", self.ssim_min)
        self.f.addRow("Diff threshold", self.diff_thr)
        self.f.addRow("Min blob area", self.min_blob)
        self.f.addRow("Max total area", self.max_area)
        self.f.addRow("Max blob count", self.max_cnt)

        row = QHBoxLayout()
        self.btn_load = QPushButton("Načítať z DB")
        self.btn_save = QPushButton("Uložiť → DB/JSON")
        row.addWidget(self.btn_load); row.addWidget(self.btn_save)
        self.f.addRow(row)

        self.btn_load.clicked.connect(self.load_from_db)
        self.btn_save.clicked.connect(self.save_to_db)

        self.refresh_from_tool()

    def refresh_from_tool(self):
        th = self.mw.tool.thresholds
        self.ssim_min.setValue(float(th.get("ssim_min", 0.92)))
        self.diff_thr.setValue(int(th.get("diff_thresh", 15)))
        self.min_blob.setValue(int(th.get("min_blob_area", 20)))
        self.max_area.setValue(int(th.get("max_total_area", 2000)))
        self.max_cnt.setValue(int(th.get("max_blob_count", 10)))

    def load_from_db(self):
        rid = self.mw.db.recipe_id(self.mw.current_recipe_name())
        if rid is None:
            return
        th = self.mw.db.get_thresholds(rid)
        if th:
            self.mw.tool.thresholds.update(th)
            self.mw.tool.save_thresholds()
            self.refresh_from_tool()

    def save_to_db(self):
        th = dict(
            ssim_min=self.ssim_min.value(),
            diff_thresh=self.diff_thr.value(),
            min_blob_area=self.min_blob.value(),
            max_total_area=self.max_area.value(),
            max_blob_count=self.max_cnt.value(),
        )
        self.mw.tool.thresholds.update(th)
        self.mw.tool.save_thresholds()
        rid = self.mw.db.recipe_id(self.mw.current_recipe_name())
        if rid is not None:
            self.mw.db.set_thresholds(rid, self.mw.tool.thresholds)
