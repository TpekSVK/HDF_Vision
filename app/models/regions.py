# app/models/regions.py
from dataclasses import dataclass, asdict
from typing import List, Literal, Dict, Any, Tuple

RegionType = Literal["pose", "roi", "ignore"]
ShapeType = Literal["rect", "circle", "poly"]

@dataclass
class Region:
    reg_type: RegionType   # "pose" | "roi" | "ignore"
    shape:    ShapeType    # "rect" | "circle" | "poly"
    # geometry:
    # - rect:   [x, y, w, h]
    # - circle: [cx, cy, r]
    # - poly:   [[x1,y1], [x2,y2], ...]
    geom: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Region":
        return Region(reg_type=d["reg_type"], shape=d["shape"], geom=d["geom"])

def validate_cardinality(regions: List["Region"]) -> Tuple[bool, str]:
    pose = sum(1 for r in regions if r.reg_type == "pose")
    roi  = sum(1 for r in regions if r.reg_type == "roi")
    ign  = sum(1 for r in regions if r.reg_type == "ignore")
    if pose != 1:
        return False, "Musí byť presne 1× Modrá (pose)."
    if roi != 1:
        return False, "Musí byť presne 1× Zelená (ROI)."
    if ign > 5:
        return False, "Magenta (ignore) najviac 5×."
    return True, "OK"
