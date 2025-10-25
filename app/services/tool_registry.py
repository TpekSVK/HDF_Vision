"""Central registry for tool implementations and metadata."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.models.schema import (
    Tool,
    ToolDefinition,
    ToolMetaDefinition,
    ToolMetricSpec,
    ToolMask,
    ToolParams,
    ToolRoi,
    ToolSchema,
    ToolSchemaField,
    ToolThresholds,
)
from app.tools.light_presence import LightPresenceCheckTool
from app.tools.light_transmission import LightTransmissionCheckTool

logger = logging.getLogger(__name__)


def _normalize_choices(raw_choices: Any) -> tuple[tuple[Any, Any], ...]:
    if not raw_choices:
        return ()
    choices: list[tuple[Any, Any]] = []
    for entry in raw_choices:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            choices.append((entry[0], entry[1]))
        else:
            choices.append((entry, entry))
    return tuple(choices)


def _coerce_schema_fields(fields: Dict[str, Dict[str, Any]] | None) -> tuple[ToolSchemaField, ...]:
    if not fields:
        return ()
    normalized: list[ToolSchemaField] = []
    for name, spec in fields.items():
        if not isinstance(spec, dict):
            spec = {"default": spec}
        normalized.append(
            ToolSchemaField(
                name=name,
                type=str(spec.get("type", "")),
                default=spec.get("default"),
                label=spec.get("label"),
                description=spec.get("description", ""),
                min=spec.get("min"),
                max=spec.get("max"),
                step=spec.get("step"),
                required=bool(spec.get("required", False)),
                choices=_normalize_choices(spec.get("choices")),
            )
        )
    return tuple(normalized)


def _coerce_metrics(metrics: Iterable[Dict[str, Any]] | None) -> tuple[ToolMetricSpec, ...]:
    if not metrics:
        return ()
    converted: list[ToolMetricSpec] = []
    for spec in metrics:
        converted.append(
            ToolMetricSpec(
                key=str(spec.get("key")),
                unit=spec.get("unit"),
                priority=int(spec.get("priority", 0)),
                description=str(spec.get("description", "")),
            )
        )
    return tuple(converted)


class ToolRegistry:
    """Registry that keeps track of tool factories and metadata."""

    _definitions: Dict[str, ToolDefinition] = {}
    _factories: Dict[str, Callable[[], "ITool"]] = {}
    _aliases: Dict[str, str] = {}

    _SUPPORTED_FIELD_TYPES = {"int", "float", "bool", "enum", "roi"}

    @classmethod
    def register(cls, type_id: str, factory: Callable[[], "ITool"], meta: Dict[str, Any]) -> None:
        schema = ToolSchema(
            params=_coerce_schema_fields(meta.get("schema", {}).get("params")),
            thresholds=_coerce_schema_fields(meta.get("schema", {}).get("thresholds")),
        )
        definition = ToolDefinition(
            type_id=type_id,
            name=meta.get("name", type_id),
            description=meta.get("description", ""),
            category=meta.get("category", "General"),
            deprecated=bool(meta.get("deprecated", False)),
            meta=ToolMetaDefinition(
                supports_roi=bool(meta.get("supports_roi", False)),
                supports_ignore_mask=bool(meta.get("supports_ignore_mask", False)),
                schema=schema,
                extra={k: v for k, v in meta.items() if k not in {"schema", "metrics_spec"}},
            ),
            metrics_spec=_coerce_metrics(meta.get("metrics_spec")),
        )

        cls._definitions[type_id] = definition
        cls._factories[type_id] = factory

    @classmethod
    def alias(cls, alias_id: str, target_id: str) -> None:
        cls._aliases[alias_id] = target_id

    @classmethod
    def list_tool_types(cls, include_aliases: bool = False) -> List[str]:
        """Return registered tool identifiers.

        By default aliases are excluded so the UI presents each tool only
        once. Callers that need legacy alias identifiers can opt-in by
        setting ``include_aliases`` to ``True``.
        """

        known = set(cls._definitions.keys())
        if include_aliases:
            known |= set(cls._aliases.keys())
        return sorted(known)

    @classmethod
    def _resolve(cls, type_id: str) -> str:
        return cls._aliases.get(type_id, type_id)

    @classmethod
    def get_tool_definition(cls, type_id: str) -> Optional[ToolDefinition]:
        return cls._definitions.get(cls._resolve(type_id))

    @classmethod
    def get_tool_schema(cls, type_id: str) -> Dict[str, Dict[str, Any]]:
        definition = cls.get_tool_definition(type_id)
        if definition is None:
            raise KeyError(f"Tool type '{type_id}' is not registered")

        schema = definition.meta.schema

        def _fields_to_dict(fields: tuple[ToolSchemaField, ...]) -> Dict[str, Dict[str, Any]]:
            result: Dict[str, Dict[str, Any]] = {}
            for field in fields:
                data = asdict(field)
                data.pop("name", None)
                result[field.name] = data
            return result

        return {
            "params": _fields_to_dict(schema.params),
            "thresholds": _fields_to_dict(schema.thresholds),
        }

    @classmethod
    def create_tool(cls, type_id: str) -> "ITool":
        resolved = cls._resolve(type_id)
        factory = cls._factories.get(resolved)
        if factory is None:
            raise KeyError(f"Tool type '{type_id}' is not registered")
        return factory()

    @classmethod
    def make_default_tool(cls, tool_type: str, name: Optional[str] = None) -> Tool:
        definition = cls.get_tool_definition(tool_type)
        if definition is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")

        params = {
            field.name: deepcopy(field.default)
            for field in definition.meta.schema.params
            if field.default is not None
        }
        thresholds = {
            field.name: deepcopy(field.default)
            for field in definition.meta.schema.thresholds
            if field.default is not None
        }

        return Tool(
            type=tool_type,
            name=name or definition.name,
            roi=ToolRoi(),
            ignore_mask=ToolMask() if definition.meta.supports_ignore_mask else ToolMask(None),
            params=ToolParams(params),
            thresholds=ToolThresholds(thresholds),
        )


from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from app.services.tool_service import ITool


def _log_registered_tools() -> None:
    entries = [
        f"{tool_id} (deprecated)" if definition.deprecated else tool_id
        for tool_id, definition in ToolRegistry._definitions.items()
    ]
    if entries:
        logger.info("Registered tools: %s", ", ".join(sorted(entries)))


def _register_default_tools() -> None:
    from app.services import tool_service
    from app.services.tools import edge, mse, ncc, ssd

    ToolRegistry.register(
        "ssim",
        factory=lambda: tool_service.SSIMTool(),
        meta={
            "name": "SSIM",
            "description": "Porovnanie štrukturálnej podobnosti v ROI.",
            "category": "Similarity",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {},
                "thresholds": {
                    "ssim_min": {
                        "type": "float",
                        "default": 0.92,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "required": True,
                        "description": "Minimálna povolená hodnota štrukturálnej podobnosti (SSIM).",
                    }
                },
            },
            "metrics_spec": [
                {"key": "ssim", "unit": None, "priority": 10, "description": "SSIM hodnota"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "locator.template_match",
        factory=lambda: tool_service.LocatorTemplateMatchTool(),
        meta={
            "name": "Locator (Template Match)",
            "description": "Vyhľadávanie šablóny s podporou search a template ROI.",
            "category": "Locator",
            "supports_roi": True,
            "supports_ignore_mask": False,
            "schema": {
                "params": {
                    "use_golden_crop": {
                        "type": "bool",
                        "default": True,
                        "description": "Použiť golden snapshot ako šablónu bez manuálneho výrezu.",
                    },
                    "coarse_to_fine": {
                        "type": "bool",
                        "default": True,
                        "description": "Povoliť dvojfázové hľadanie od hrubého po jemné zarovnanie.",
                    },
                    "coarse_cap": {
                        "type": "int",
                        "default": 600,
                        "min": 64,
                        "max": 4096,
                        "step": 16,
                        "description": "Maximálna veľkosť hrubého search okna (px).",
                    },
                    "rotation_enabled": {
                        "type": "bool",
                        "default": False,
                        "label": "Kompenzovať rotáciu",
                        "description": "Kompenzovať rotáciu počas template matchingu.",
                    },
                    "angle_range_deg": {
                        "type": "float",
                        "default": 15.0,
                        "min": 0.0,
                        "max": 180.0,
                        "step": 0.5,
                        "precision": 1,
                        "label": "Angle range (±°)",
                        "description": "Maximálny absolútny uhol odchýlky (±°).",
                    },
                    "angle_step_deg": {
                        "type": "float",
                        "default": 1.0,
                        "min": 0.01,
                        "max": 45.0,
                        "step": 0.25,
                        "precision": 2,
                        "label": "Angle step (°)",
                        "description": "Krok vyhľadávania uhlov (°).",
                    },
                    "apply_alignment": {
                        "type": "bool",
                        "default": True,
                        "description": "Aplikovať výsledné posunutie na nasledujúce nástroje.",
                    },
                    "template_roi": {
                        "type": "roi",
                        "default": None,
                        "description": "Manuálne definovaný template výrez na golden obrázku.",
                    },
                },
                "thresholds": {
                    "threshold_corr": {
                        "type": "float",
                        "default": 0.55,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "description": "Minimálna korelácia potrebná na úspešné zarovnanie.",
                    }
                },
            },
            "metrics_spec": [
                {"key": "corr", "priority": 10, "description": "Korelácia"},
                {"key": "dx", "priority": 8, "description": "Posun na osi X", "unit": "px"},
                {"key": "dy", "priority": 8, "description": "Posun na osi Y", "unit": "px"},
                {"key": "theta_deg", "priority": 6, "description": "Otočenie", "unit": "°"},
                {"key": "latency_ms", "priority": 1, "description": "Čas behu", "unit": "ms"},
            ],
        },
    )

    ToolRegistry.register(
        "ssd",
        factory=lambda: ssd.SSDTool(),
        meta={
            "name": "SSD",
            "description": "Súčet štvorcov rozdielov v ROI s voliteľným rozmazaním.",
            "category": "Similarity",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {
                    "preblur_sigma": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 10.0,
                        "step": 0.1,
                        "description": "Sigma pre Gaussian blur pred porovnaním.",
                    }
                },
                "thresholds": {
                    "ssd_max": {
                        "type": "float",
                        "default": 1.0e7,
                        "min": 0.0,
                        "description": "Maximálna povolená hodnota súčtu štvorcov rozdielov.",
                    }
                },
            },
            "metrics_spec": [
                {"key": "ssd", "unit": None, "priority": 10, "description": "SSD hodnota"},
                {"key": "mean_abs", "unit": None, "priority": 5, "description": "Priemerný absolútny rozdiel"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "mse",
        factory=lambda: mse.MSETool(),
        meta={
            "name": "MSE",
            "description": "Mean Squared Error medzi golden a snímkou v ROI.",
            "category": "Similarity",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {
                    "preblur_sigma": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 10.0,
                        "step": 0.1,
                        "description": "Sigma pre Gaussian blur pred meraním.",
                    }
                },
                "thresholds": {
                    "mse_max": {
                        "type": "float",
                        "default": 25.0,
                        "min": 0.0,
                        "description": "Maximálna povolená MSE hodnota.",
                    }
                },
            },
            "metrics_spec": [
                {"key": "mse", "unit": None, "priority": 10, "description": "MSE hodnota"},
                {"key": "rmse", "unit": None, "priority": 5, "description": "Koreň strednej chyby"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "ncc",
        factory=lambda: ncc.NCCTool(),
        meta={
            "name": "NCC",
            "description": "Normalizovaná krížová korelácia v rámci ROI.",
            "category": "Similarity",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {
                    "preblur_sigma": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 10.0,
                        "step": 0.1,
                        "description": "Sigma pre Gaussian blur pred koreláciou.",
                    }
                },
                "thresholds": {
                    "ncc_min": {
                        "type": "float",
                        "default": 0.9,
                        "min": -1.0,
                        "max": 1.0,
                        "step": 0.01,
                        "description": "Minimálna povolená NCC hodnota.",
                    }
                },
            },
            "metrics_spec": [
                {"key": "ncc", "unit": None, "priority": 10, "description": "NCC hodnota"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "light_presence",
        factory=lambda: LightPresenceCheckTool(),
        meta={
            "name": "Kontrola svetlej plochy (otvor)",
            "description": "Backlight kontrola prítomnosti otvoru: spočíta percento/počet bielych pixelov v ROI. Nastav prah a min/max plochu.",
            "category": "Presence / Backlight",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "catalog_label": "Kontrola svetlej plochy (otvor)",
            "catalog_short": "Binarizácia ROI a meranie plochy bielych pixelov.",
            "catalog_tooltip": "Backlight kontrola prítomnosti otvoru: spočíta percento/počet bielych pixelov v ROI. Nastav prah a min/max plochu.",
            "schema": {
                "params": {
                    "binary_threshold": {
                        "type": "int",
                        "default": 200,
                        "min": 0,
                        "max": 255,
                        "step": 1,
                        "label": "Binary threshold",
                        "description": "Prahová hodnota pre binarizáciu (0 – 255).",
                    },
                    "min_area_px": {
                        "type": "int",
                        "default": 100,
                        "min": 0,
                        "label": "Min. area [px]",
                        "description": "Minimálny počet svetlých pixelov potrebný pre OK.",
                    },
                    "max_area_px": {
                        "type": "int",
                        "default": 10_000,
                        "min": 0,
                        "label": "Max. area [px]",
                        "description": "Maximálny počet svetlých pixelov povolený pre OK.",
                    },
                    "gaussian_blur_kernel": {
                        "type": "enum",
                        "default": 0,
                        "label": "Gaussian blur",
                        "description": "Voliteľný Gaussian blur pred binarizáciou.",
                        "choices": [
                            (0, "Vypnuté"),
                            (3, "3×3"),
                            (5, "5×5"),
                        ],
                    },
                },
                "thresholds": {},
            },
            "metrics_spec": [
                {
                    "key": "area_px",
                    "unit": "px",
                    "priority": 10,
                    "description": "Počet svetlých pixelov",
                },
                {
                    "key": "threshold",
                    "priority": 5,
                    "description": "Použitý binárny prah",
                },
                {
                    "key": "latency_ms",
                    "unit": "ms",
                    "priority": 1,
                    "description": "Čas behu",
                },
            ],
        },
    )

    ToolRegistry.register(
        "light_transmission",
        factory=lambda: LightTransmissionCheckTool(),
        meta={
            "name": "Light Transmission Check",
            "description": "Zmeria štatistiky intenzity (odtiene šedej) a percento pixelov nad prahom.",
            "category": "Presence / Backlight",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "catalog_label": "Priepustnosť svetla (odtiene šedej)",
            "catalog_short": "Zmeria mean/median/std v ROI vrátane % pixelov nad prahom.",
            "catalog_tooltip": "Štatistika intenzity v ROI; funguje pre Y8 aj Y12. Pri kalibrácii sa prah interpretuje v %.",
            "schema": {
                "params": {
                    "calibration_enabled": {
                        "type": "bool",
                        "default": False,
                        "label": "Kalibrácia (%)",
                        "description": "Zapni mapovanie na percentá (využije čiernu/bielu referenciu).",
                    },
                    "calibration_dark_gray": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 255.0,
                        "label": "Dark level [0-255]",
                        "description": "Hodnota zodpovedajúca 0 % priepustnosti.",
                    },
                    "calibration_bright_gray": {
                        "type": "float",
                        "default": 255.0,
                        "min": 0.0,
                        "max": 255.0,
                        "label": "Bright level [0-255]",
                        "description": "Hodnota zodpovedajúca 100 % priepustnosti.",
                    },
                    "threshold_value": {
                        "type": "float",
                        "default": 128.0,
                        "min": 0.0,
                        "max": 255.0,
                        "label": "Prahová hodnota T",
                        "description": "Prahová hodnota v šedej [0-255] alebo v % pri zapnutej kalibrácii.",
                    },
                },
                "thresholds": {
                    "min_mean_gray": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 255.0,
                        "label": "Min. mean",
                        "description": "Minimálna mean hodnota (alebo % pri kalibrácii).",
                    },
                    "max_mean_gray": {
                        "type": "float",
                        "default": 255.0,
                        "min": 0.0,
                        "max": 255.0,
                        "label": "Max. mean",
                        "description": "Maximálna mean hodnota (alebo % pri kalibrácii).",
                    },
                    "min_pct_above_T": {
                        "type": "float",
                        "default": 0.0,
                        "min": 0.0,
                        "max": 100.0,
                        "label": "Min. % nad T",
                        "description": "Minimálny podiel pixelov nad prahom T [%].",
                    },
                    "max_pct_above_T": {
                        "type": "float",
                        "default": 100.0,
                        "min": 0.0,
                        "max": 100.0,
                        "label": "Max. % nad T",
                        "description": "Maximálny podiel pixelov nad prahom T [%].",
                    },
                },
            },
            "metrics_spec": [
                {"key": "mean_gray", "unit": None, "priority": 10, "description": "Priemerná intenzita"},
                {"key": "median_gray", "unit": None, "priority": 9, "description": "Medián intenzity"},
                {"key": "min_gray", "unit": None, "priority": 7, "description": "Minimum"},
                {"key": "max_gray", "unit": None, "priority": 7, "description": "Maximum"},
                {"key": "std_gray", "unit": None, "priority": 6, "description": "Štandardná odchýlka"},
                {"key": "p10_gray", "unit": None, "priority": 5, "description": "10. percentil"},
                {"key": "p90_gray", "unit": None, "priority": 5, "description": "90. percentil"},
                {"key": "pct_above_T", "unit": "%", "priority": 8, "description": "% pixelov nad T"},
                {"key": "pct_below_T", "unit": "%", "priority": 4, "description": "% pixelov pod T"},
                {"key": "mean_transmission_pct", "unit": "%", "priority": 3, "description": "Priemerná priepustnosť (%)"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "edge_change",
        factory=lambda: edge.EdgeChangeTool(),
        meta={
            "name": "Edge Change",
            "description": "Vyhodnotenie hrán a rozdielov cez thresholdovaný absdiff.",
            "category": "Change Detection",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {
                    "blur_sigma": {
                        "type": "float",
                        "default": 1.0,
                        "min": 0.0,
                        "max": 10.0,
                        "step": 0.1,
                        "description": "Sigma pre Gaussian blur pred prahovaním.",
                    },
                    "diff_threshold": {
                        "type": "int",
                        "default": 25,
                        "min": 0,
                        "max": 255,
                        "description": "Prah absolútneho rozdielu pre detekciu hrán.",
                    },
                    "use_morphology": {
                        "type": "bool",
                        "default": False,
                        "description": "Povoliť open+dilate na očistenie masky hrán.",
                    },
                    "morph_open": {
                        "type": "int",
                        "default": 3,
                        "min": 1,
                        "max": 15,
                        "description": "Veľkosť jadra pre operáciu open.",
                    },
                    "morph_dilate": {
                        "type": "int",
                        "default": 3,
                        "min": 1,
                        "max": 15,
                        "description": "Veľkosť jadra pre dilatáciu.",
                    },
                },
                "thresholds": {
                    "edge_ratio_max": {
                        "type": "float",
                        "default": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.005,
                        "description": "Maximálny podiel hrán označených ako zmena.",
                    }
                },
            },
            "metrics_spec": [
                {"key": "edge_ratio", "unit": None, "priority": 10, "description": "Podiel zmenených hrán"},
                {"key": "mean_diff", "unit": None, "priority": 5, "description": "Priemerný absolútny rozdiel"},
                {"key": "latency_ms", "unit": "ms", "priority": 1, "description": "Čas behu"},
            ],
        },
    )

    ToolRegistry.register(
        "absdiff",
        factory=lambda: tool_service.AbsDiffTool(),
        meta={
            "name": "Abs Diff",
            "description": "Porovnanie absolútnych rozdielov s blob analýzou.",
            "category": "Inspection",
            "supports_roi": True,
            "supports_ignore_mask": True,
            "schema": {
                "params": {},
                "thresholds": {
                    "diff_thresh": {"type": "int", "default": 15, "min": 0, "max": 255, "step": 1},
                    "min_blob_area": {
                        "type": "int",
                        "default": 20,
                        "min": 0,
                        "max": 1_000_000,
                        "step": 1,
                    },
                    "max_total_area": {
                        "type": "int",
                        "default": 2000,
                        "min": 0,
                        "max": 10_000_000,
                        "step": 1,
                    },
                    "max_blob_count": {
                        "type": "int",
                        "default": 10,
                        "min": 0,
                        "max": 10_000,
                        "step": 1,
                    },
                },
            },
            "metrics_spec": [
                {"key": "blob_count", "priority": 9, "description": "Počet blobov"},
                {"key": "latency_ms", "priority": 1, "description": "Čas behu", "unit": "ms"},
            ],
        },
    )

    ToolRegistry.alias("template_match", "locator.template_match")
    logger.warning(
        "Tool 'template_match' is deprecated. Use 'locator.template_match' instead.")

    _log_registered_tools()


_register_default_tools()

