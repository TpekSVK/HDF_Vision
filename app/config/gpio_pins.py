"""Central fixed GPIO mapping for device-level wiring (Jetson BOARD numbering)."""

GPIO_BOARD = "JETSON_ORIN_NANO"

TRIGGER_OUT_PIN = 7
TRIGGER_PULSE_MS = 10

# Optional fixed mappings for future extension (currently unused by UI flow).
TRIGGER_IN_PIN = None
HEARTBEAT_OUT_PIN = None
RESULT_OK_OUT_PIN = None
RESULT_NOK_OUT_PIN = None
FLASH1_OUT_PIN = None
FLASH2_OUT_PIN = None
