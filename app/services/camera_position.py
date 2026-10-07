"""Read-only camera diagnostics; never run the inspection/result pipeline."""
from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import imageio.v3 as iio

from app.services.storage_service import load_recipe_config
from app.services.tools.locator_template import run_locator_template_match
from app.services.view_capture import ViewCapture
from app.services.view_images import apply_view_rotation, view_image_rotation
from app.services.camera_profiles import apply_view_camera_profile
from app.utils.imaging import to_gray_u8
from app.models.schema import RecipeV2, RecipeView, Tool


@dataclass(frozen=True)
class PositionLimits:
    x: float | None
    y: float | None
    rotation: float | None


@dataclass(frozen=True)
class CameraPositionResult:
    dx_px: float | None = None
    dy_px: float | None = None
    rotation_deg: float | None = None
    confidence: float | None = None
    x_within_tolerance: bool | None = None
    y_within_tolerance: bool | None = None
    rotation_within_tolerance: bool | None = None
    overall_ok: bool | None = None
    corrections: tuple[str, ...] = ()
    invalid_reason: str | None = None


def _limit(values, key):
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def alignment_limits(tool):
    """Only saved acceptance limits, never search ranges or registry defaults."""
    thresholds, params = tool.thresholds.values, tool.params.values
    rotation = (_limit(params, 'reference_max_angle_deg')
                if params.get('alignment_mode') == 'guided_edge' else None)
    return PositionLimits(_limit(thresholds, 'max_shift_x'),
                          _limit(thresholds, 'max_shift_y'), rotation)


def camera_correction_from_alignment(dx, dy, rotation, outside, view_rotation=0):
    """Golden -> current, x right/y down/theta clockwise in image coordinates.

    A physical camera translation moves a stationary target in the opposite
    direction. Therefore the camera correction has the SAME sign as image
    displacement, not the inverse affine translation. Undo the saved clockwise
    View orientation before naming physical camera axes. Camera roll has the
    same correction sign as observed image roll. Directions assume a fixed
    target and camera-plane axes seen from behind the camera towards the target.
    """
    angle = math.radians(view_rotation)
    # Only corrections for recipe axes which are outside their saved limits.
    x, y = dx if outside[0] else 0.0, dy if outside[1] else 0.0
    raw_x = math.cos(angle) * x + math.sin(angle) * y
    raw_y = -math.sin(angle) * x + math.cos(angle) * y
    instructions = []
    if abs(raw_x) > 1e-9:
        instructions.append('→ POSUŇ KAMERU DOPRAVA' if raw_x > 0 else '← POSUŇ KAMERU DOĽAVA')
    if abs(raw_y) > 1e-9:
        instructions.append('↓ POSUŇ KAMERU DOLE' if raw_y > 0 else '↑ POSUŇ KAMERU HORE')
    if outside[2] and rotation is not None:
        instructions.append('↻ OTOČ KAMERU V SMERE HODINOVÝCH RUČIČIEK' if rotation > 0
                            else '↺ OTOČ KAMERU PROTI SMERU HODINOVÝCH RUČIČIEK')
    return tuple(instructions)


def evaluate_alignment(dx, dy, rotation, confidence, limits, *, reliable=True,
                       view_rotation=0, reason=None):
    values = (dx, dy, rotation)
    if not reliable or any(v is not None and not math.isfinite(v) for v in (*values, confidence)):
        return CameraPositionResult(confidence=confidence, invalid_reason=reason or 'Referencia sa nenašla spoľahlivo.')
    checks = tuple(None if value is None or limit is None else abs(value) <= limit
                   for value, limit in zip(values, (limits.x, limits.y, limits.rotation)))
    overall = False if False in checks else (True if all(v is True for v in checks) else None)
    corrections = camera_correction_from_alignment(dx, dy, rotation,
        tuple(v is False for v in checks), view_rotation)
    return CameraPositionResult(dx, dy, rotation, confidence, *checks, overall, corrections)


@dataclass(frozen=True)
class PositionReference:
    recipe_name: str
    recipe: RecipeV2
    view: RecipeView
    locator: Tool | None
    golden: np.ndarray
    raw_golden: np.ndarray
    limits: PositionLimits


class CameraPositionService:
    def __init__(self, recipes, camera, pico, pico_config):
        self.recipes, self.camera, self.pico, self.pico_config = recipes, camera, pico, pico_config
        self._session_key = None
        self._session_mode = None

    def open_reference(self, recipe_name, view_id, mode):
        reference = self.load_reference(recipe_name, view_id)
        self.prepare_session(reference, mode)
        return reference

    def prepare_session(self, reference, mode):
        """Prepare once on entry; a different camera profile needs a transition."""
        if mode not in {'master', 'trigger'}:
            raise ValueError('Neznámy režim snímania.')
        key = (mode, reference.view.to_dict()['camera_profile'])
        if self._session_key == key:
            return
        self.close_session()
        if not self.pico.is_available() and not self.pico.connect():
            raise RuntimeError('Pico nie je dostupné.')
        self._session_mode = mode
        try:
            apply_view_camera_profile(self.camera, {}, reference.view.camera_profile)
            if mode == 'trigger':
                self.pico.prepare_trigger(self.camera)
            else:
                self.pico.prepare_master(self.camera)
            self._session_key = key
        except Exception:
            self.close_session()
            raise

    def close_session(self):
        """Worker-only cleanup on departure or a hardware/capture error."""
        if self._session_mode is None:
            return
        try:
            self.pico.quiesce()
        finally:
            if self._session_mode == 'trigger':
                self.camera.exit_trigger_session(restore_master=False)
        # Keep ownership on cleanup failure so the pause/close path can retry.
        self._session_mode = None
        self._session_key = None

    def load_reference(self, recipe_name, view_id=None):
        # Same active recipe document as InspectionRuntime; no RecipeService.load
        # (which mutates the active ToolService), no save/publish/audit operation.
        recipe = load_recipe_config(recipe_name, base_dir=self.recipes.base)
        view = recipe.get_view(view_id)
        locators = [t for t in sorted(view.tools, key=lambda t: t.order)
                    if t.enabled and t.type == 'locator.template_match']
        locator = locators[0] if len(locators) == 1 else None
        root = (Path(self.recipes.base) / 'recipes' / recipe_name).resolve()
        path = (root / view.golden_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Golden musí patriť aktívnemu receptu.')
        golden = np.asarray(iio.imread(path)).copy()
        # Golden Wizard persists unaligned pixels after the lossless View rotation.
        raw = apply_view_rotation(golden, (-view_image_rotation(view)) % 360)
        limits = alignment_limits(locator) if locator else PositionLimits(None, None, None)
        return PositionReference(recipe_name, recipe, view, locator, golden, raw, limits)

    def compare(self, reference, raw_frame):
        if reference.locator is None:
            return CameraPositionResult(invalid_reason='Recept potrebuje práve jeden aktívny locator pre zvolený pohľad.')
        frame = apply_view_rotation(raw_frame, view_image_rotation(reference.view))
        if frame.shape[:2] != reference.golden.shape[:2]:
            return CameraPositionResult(invalid_reason='Rozmery snímky a goldenu sa nezhodujú.')
        tool = reference.locator.copy()
        params, thresholds = dict(tool.params.values), dict(tool.thresholds.values)
        if params.get('use_golden_crop', False):
            params['template_roi'] = tool.roi.to_dict()
            params['use_golden_crop'] = False
        elif 'template_roi' not in params:
            params['template_roi'] = tool.template_roi.to_dict()
        # Full-frame diagnostic search; all changes are private runtime copies.
        # Retain measured shifts outside production limits rather than dropping
        # them as "not found". Keep the detector's correlation/coverage rules.
        thresholds.update(max_shift_x=float('inf'), max_shift_y=float('inf'))
        if params.get('alignment_mode') == 'guided_edge':
            params['reference_max_angle_deg'] = 90.0
        h, w = frame.shape[:2]
        _, diagnostics = run_locator_template_match(to_gray_u8(reference.golden), to_gray_u8(frame), params,
            thresholds, {'x': 0, 'y': 0, 'w': w, 'h': h})
        mode = params.get('alignment_mode', 'translation')
        rotation = diagnostics['theta_deg'] if mode != 'translation' else None
        reliable = bool(diagnostics['found']) and diagnostics['corr'] >= diagnostics['threshold_corr']
        reliable = reliable and not diagnostics.get('quality_warnings')
        # A rotation at the edge of a finite search cannot rule out larger roll.
        if mode == 'template_rotation' and abs(rotation) >= params.get('angle_range_deg', 15.0):
            reliable = False
        return evaluate_alignment(diagnostics['dx'], diagnostics['dy'], rotation,
            diagnostics['corr'], reference.limits, reliable=reliable,
            view_rotation=view_image_rotation(reference.view))

    def capture(self, reference, mode):
        """Shared camera/Pico path; no RUN runner, DB, counters or Modbus handle."""
        cam, pico = self.camera, self.pico
        trigger_started = False
        try:
            self.prepare_session(reference, mode)
            if mode == 'trigger':
                cam.begin_trigger_capture()
                trigger_started = True
            try:
                raw = ViewCapture(cam, pico, self.pico_config, mode, reference.view.id).capture(
                    trigger_mode_label='diagnostic', master_caller='camera_position',
                    view=reference.view, image_rotation_override=0,
                    capture_request_source='diagnostic', transform_stage='', hardware_prepared=True)
                raw = np.asarray(raw).copy()
                return raw, self.compare(reference, raw)
            finally:
                if trigger_started:
                    cam.end_trigger_capture()
        except Exception:
            self.close_session()
            raise
