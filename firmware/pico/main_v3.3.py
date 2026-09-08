"""HDF Vision Raspberry Pi Pico controller firmware v3.3.

MicroPython firmware for LED/light and See3CAM_CU55M trigger control.
Copy this file to the Pico as main.py.

MASTER production capture protocol:
- physical inputs emit ``CAPTURE IN1`` through ``CAPTURE IN8``;
- ``FIRE V1`` and ``FIRE V2`` execute the same configured MASTER timing
  (DELAY -> LIGHT ON -> CAPTURE -> LIGHT OFF) and emit ``CAPTURE V1`` or
  ``CAPTURE V2`` at the configured CAPTURE delay;
- ``TRIGGER IN1`` through ``TRIGGER IN8`` is the software equivalent of a
  physical input edge. It uses the configured INx -> V1/V2 mapping and emits
  the same ``CAPTURE INx`` event as the physical production path;
- existing TRIGGER mode behaviour, camera pulses and V1/V2 mapping are
  unchanged.

Hardware pins:
- GP17 = LED / light output, active HIGH
- GP16 = CU55 camera TRIG input, active HIGH
- GP1..GP8 = external inputs, active LOW

Serial protocol used by HDF_Vision:
- STATUS
- SAVE
- INPUTS
- FIRE V1                # MASTER: production timing + CAPTURE V1
- FIRE V2                # MASTER: production timing + CAPTURE V2
- TRIGGER IN1..IN8       # software request through configured input mapping
- SET V1 MODE TRIGGER|MASTER
- SET V1 DELAY <ms>
- SET V1 PULSE <ms>      # light ON duration for whole sequence
- SET V1 CAPTURE <ms>    # MASTER: delay LIGHT ON -> CAPTURE INx USB event
- SET V1 TRIG <ms>       # camera trigger HIGH pulse width
- SET V1 GAP <ms>        # delay between camera trigger pulses
- SET V1 COUNT <n>       # number of trigger pulses; default 2
- SET V1 SINGLE 1        # shortcut COUNT=1
- SET V1 DOUBLE 1        # shortcut COUNT=2
- MAP ALL OFF
- MAP IN1 V1|V2|OFF
- LIGHT ON                # manual light override ON
- LIGHT OFF               # manual light override OFF
- LIGHT STATUS            # current manual light state

MASTER mode capture flow:
    physical INx edge, TRIGGER INx, or FIRE V1/V2
        -> DELAY
        -> LIGHT ON
        -> CAPTURE delay
        -> print "CAPTURE <source>"
        -> keep light ON until PULSE duration is satisfied
        -> LIGHT OFF

Important:
- ``FIRE V1/V2`` is the software request for a selected sequential profile.
  In MASTER it emits ``CAPTURE V1/V2``; Jetson resolves this to the view that
  initiated the request.
- ``TRIGGER INx`` is the software request for an explicit view. In MASTER it
  emits ``CAPTURE INx`` just like its mapped physical input.
- ``SIM INx`` remains a deprecated compatibility alias for ``TRIGGER INx``.
- Manual LIGHT ON/OFF is runtime-only and is never saved to flash.
- When manual light is ON, normal FIRE/physical cycles keep the light ON after the cycle.
"""

import json
import select
import sys
import time

from machine import Pin

FIRMWARE_NAME = "pico_hdf_controller"
FIRMWARE_VERSION = "3.3-master-production-capture"
CONFIG_FILE = "hdf_pico_config.json"

LED_PIN = 17
CAM_TRIG_PIN = 16
INPUT_PINS = {
    "IN1": 1,
    "IN2": 2,
    "IN3": 3,
    "IN4": 4,
    "IN5": 5,
    "IN6": 6,
    "IN7": 7,
    "IN8": 8,
}

# Defaults:
# - TRIGGER mode defaults remain tuned for CU55 intermittent trigger operation.
# - MASTER CAPTURE delay is the delay from LIGHT ON to the asynchronous
#   "CAPTURE INx" event sent to HDF_Vision over USB.
DEFAULT_CONFIG = {
    "V1_MODE": "TRIGGER",
    "V2_MODE": "TRIGGER",
    "V1_DELAY": 0,
    "V2_DELAY": 0,
    "V1_PULSE": 200,
    "V2_PULSE": 200,
    "V1_CAPTURE": 5,
    "V2_CAPTURE": 5,
    "V1_TRIG": 10,
    "V2_TRIG": 10,
    "V1_GAP": 300,
    "V2_GAP": 300,
    "V1_COUNT": 2,
    "V2_COUNT": 2,
    "IN1": "OFF",
    "IN2": "OFF",
    "IN3": "OFF",
    "IN4": "OFF",
    "IN5": "OFF",
    "IN6": "OFF",
    "IN7": "OFF",
    "IN8": "OFF",
    "DEBOUNCE_MS": 30,
    "LOCKOUT_MS": 100,
}

led = Pin(LED_PIN, Pin.OUT)
cam_trig = Pin(CAM_TRIG_PIN, Pin.OUT)
inputs = {}
config = {}
busy = False
busy_until_ms = 0
manual_light_enabled = False
previous_input_state = {}
stdin_poll = select.poll()
stdin_poll.register(sys.stdin, select.POLLIN)


def now_ms():
    return time.ticks_ms()


def diff_ms(a, b):
    return time.ticks_diff(a, b)


def sleep_ms(ms):
    if ms > 0:
        time.sleep_ms(int(ms))


def clamp_int(value, default, min_value, max_value):
    try:
        iv = int(value)
    except (TypeError, ValueError):
        iv = int(default)
    if iv < min_value:
        return min_value
    if iv > max_value:
        return max_value
    return iv


def normalize_view(view):
    view = str(view or "").upper()
    if view in ("V1", "VIEW1", "1"):
        return "V1"
    if view in ("V2", "VIEW2", "2"):
        return "V2"
    return None


def normalize_input_name(input_name):
    value = str(input_name or "").upper()
    return value if value in INPUT_PINS else None


def normalize_config(cfg):
    for view in ("V1", "V2"):
        mode_key = view + "_MODE"
        cfg[mode_key] = str(cfg.get(mode_key, DEFAULT_CONFIG[mode_key])).upper()
        if cfg[mode_key] not in ("MASTER", "TRIGGER"):
            cfg[mode_key] = DEFAULT_CONFIG[mode_key]

        cfg[view + "_DELAY"] = clamp_int(
            cfg.get(view + "_DELAY"),
            DEFAULT_CONFIG[view + "_DELAY"],
            0,
            60000,
        )
        cfg[view + "_PULSE"] = clamp_int(
            cfg.get(view + "_PULSE"),
            DEFAULT_CONFIG[view + "_PULSE"],
            1,
            60000,
        )
        cfg[view + "_CAPTURE"] = clamp_int(
            cfg.get(view + "_CAPTURE"),
            DEFAULT_CONFIG[view + "_CAPTURE"],
            0,
            60000,
        )
        cfg[view + "_TRIG"] = clamp_int(
            cfg.get(view + "_TRIG"),
            DEFAULT_CONFIG[view + "_TRIG"],
            1,
            1000,
        )
        cfg[view + "_GAP"] = clamp_int(
            cfg.get(view + "_GAP"),
            DEFAULT_CONFIG[view + "_GAP"],
            1,
            60000,
        )
        cfg[view + "_COUNT"] = clamp_int(
            cfg.get(view + "_COUNT"),
            DEFAULT_CONFIG[view + "_COUNT"],
            1,
            10,
        )

    for input_name in INPUT_PINS:
        value = str(cfg.get(input_name, "OFF")).upper()
        cfg[input_name] = value if value in ("OFF", "V1", "V2") else "OFF"

    cfg["DEBOUNCE_MS"] = clamp_int(
        cfg.get("DEBOUNCE_MS"),
        DEFAULT_CONFIG["DEBOUNCE_MS"],
        1,
        1000,
    )
    cfg["LOCKOUT_MS"] = clamp_int(
        cfg.get("LOCKOUT_MS"),
        DEFAULT_CONFIG["LOCKOUT_MS"],
        0,
        60000,
    )
    return cfg


def merge_defaults(saved):
    merged = DEFAULT_CONFIG.copy()
    if isinstance(saved, dict):
        # v3.x flat config compatibility
        for key, value in saved.items():
            if key in merged:
                merged[key] = value

        # older v2 nested config compatibility
        views = saved.get("views") if isinstance(saved.get("views"), dict) else None
        if views:
            for view in ("V1", "V2"):
                view_cfg = views.get(view, {}) if isinstance(views.get(view), dict) else {}
                mapping = {
                    "delay_ms": view + "_DELAY",
                    "pulse_ms": view + "_PULSE",
                    "capture_delay_ms": view + "_CAPTURE",
                    "trigger_pulse_ms": view + "_TRIG",
                    "trigger_gap_ms": view + "_GAP",
                    "trigger_count": view + "_COUNT",
                }
                for old_key, new_key in mapping.items():
                    if old_key in view_cfg:
                        merged[new_key] = view_cfg[old_key]

        input_map = saved.get("input_map") if isinstance(saved.get("input_map"), dict) else None
        if input_map:
            for idx in range(1, 9):
                key = "IN{}".format(idx)
                if str(idx) in input_map:
                    merged[key] = str(input_map[str(idx)]).upper()

    return normalize_config(merged)


def load_config():
    try:
        with open(CONFIG_FILE, "r") as handle:
            return merge_defaults(json.load(handle))
    except OSError:
        return merge_defaults({})
    except ValueError:
        return merge_defaults({})


def save_config():
    with open(CONFIG_FILE, "w") as handle:
        json.dump(config, handle)
    return "OK SAVED"


def input_mapping_summary():
    parts = []
    for input_name in sorted(INPUT_PINS):
        parts.append(input_name + "=" + config.get(input_name, "OFF"))
    return " ".join(parts)


def status_lines():
    lines = [
        "FIRMWARE {} {}".format(FIRMWARE_NAME, FIRMWARE_VERSION),
        "PINS LED=GP{} TRIG=GP{}".format(LED_PIN, CAM_TRIG_PIN),
    ]
    for view in ("V1", "V2"):
        lines.extend([
            "{}_MODE {}".format(view, config[view + "_MODE"]),
            "{}_DELAY {}".format(view, config[view + "_DELAY"]),
            "{}_PULSE {}".format(view, config[view + "_PULSE"]),
            "{}_CAPTURE {}".format(view, config[view + "_CAPTURE"]),
            "{}_TRIG {}".format(view, config[view + "_TRIG"]),
            "{}_GAP {}".format(view, config[view + "_GAP"]),
            "{}_COUNT {}".format(view, config[view + "_COUNT"]),
        ])
    lines.extend([
        "INPUT_MAP " + input_mapping_summary(),
        "DEBOUNCE_MS {}".format(config["DEBOUNCE_MS"]),
        "LOCKOUT_MS {}".format(config["LOCKOUT_MS"]),
        "MANUAL_LIGHT {}".format("ON" if manual_light_enabled else "OFF"),
        "NOTE MASTER FIRE V1/V2 emits CAPTURE V1/V2",
        "NOTE TRIGGER IN1..IN8 follows physical input mapping",
        "NOTE COUNT=2 means trigger #1 dummy, trigger #2 capture",
        "END",
    ])
    return lines


def inputs_lines():
    parts = []
    for name in sorted(inputs):
        active = inputs[name].value() == 0
        parts.append("{}={}".format(name, "ACTIVE" if active else "OFF"))
    return ["INPUTS " + " ".join(parts), "END"]


def apply_light_idle_state():
    led.value(1 if manual_light_enabled else 0)


def set_outputs_idle():
    apply_light_idle_state()
    cam_trig.value(0)


def trigger_pulse(trig_ms):
    cam_trig.value(1)
    sleep_ms(trig_ms)
    cam_trig.value(0)


def normalize_capture_source(source):
    input_name = normalize_input_name(source)
    if input_name is not None:
        return input_name
    return normalize_view(source)


def emit_capture_event(source):
    source = normalize_capture_source(source)
    if source is None:
        return False

    # This line is intentionally asynchronous from the point of view of
    # HDF_Vision. The Jetson-side PicoService must treat "CAPTURE <source>"
    # as an event, not as a response to a command.
    print("CAPTURE {}".format(source))
    return True


def set_manual_light(enabled):
    global manual_light_enabled
    manual_light_enabled = bool(enabled)
    apply_light_idle_state()
    return "OK LIGHT {}".format("ON" if manual_light_enabled else "OFF")


def fire_view(view, capture_source=None):
    global busy, busy_until_ms

    view = normalize_view(view)
    if view is None:
        return "ERR VIEW"

    capture_source = normalize_capture_source(capture_source)

    now = now_ms()
    if busy or diff_ms(busy_until_ms, now) > 0:
        return "BUSY {}".format(view)

    busy = True
    try:
        mode = config[view + "_MODE"]
        delay_ms = config[view + "_DELAY"]
        pulse_ms = config[view + "_PULSE"]
        capture_ms = config[view + "_CAPTURE"]
        trig_ms = config[view + "_TRIG"]
        gap_ms = config[view + "_GAP"]
        count = config[view + "_COUNT"]
        lockout_ms = config["LOCKOUT_MS"]

        if mode == "TRIGGER":
            active_sequence_ms = max(
                pulse_ms,
                (trig_ms * count) + (gap_ms * max(0, count - 1)),
            )
        else:
            # In MASTER mode the light must still be ON when CAPTURE is emitted.
            # If CAPTURE is configured beyond PULSE, the actual light pulse is
            # extended to CAPTURE so the event can never occur after LIGHT OFF.
            active_sequence_ms = max(pulse_ms, capture_ms)

        busy_until_ms = time.ticks_add(
            now,
            delay_ms + active_sequence_ms + lockout_ms,
        )

        sleep_ms(delay_ms)

        led.value(1)
        light_on_started = now_ms()

        if mode == "TRIGGER":
            for index in range(count):
                trigger_pulse(trig_ms)
                if index < count - 1:
                    sleep_ms(gap_ms)

            light_elapsed = diff_ms(now_ms(), light_on_started)
            sleep_ms(pulse_ms - light_elapsed)

        else:
            # MASTER:
            # camera streams continuously; Pico only times the light and tells
            # HDF_Vision when a production input requests a frame.
            sleep_ms(capture_ms)

            # Every MASTER request reaches the same production capture point.
            # Physical/explicit sources use INx; direct profile requests use V1/V2.
            if capture_source is not None:
                emit_capture_event(capture_source)

            light_elapsed = diff_ms(now_ms(), light_on_started)
            sleep_ms(pulse_ms - light_elapsed)

        set_outputs_idle()

        if mode == "MASTER":
            note = "MASTER_CAPTURE" if capture_source is not None else "MASTER_NO_CAPTURE"
        else:
            note = "DOUBLE_DUMMY_CAPTURE" if count >= 2 else "SINGLE"

        return (
            "OK FIRED {} MODE={} SOURCE={} DELAY={} PULSE={} CAPTURE={} "
            "TRIG={} GAP={} COUNT={} NOTE={}"
        ).format(
            view,
            mode,
            capture_source or "USB",
            delay_ms,
            pulse_ms,
            capture_ms,
            trig_ms,
            gap_ms,
            count,
            note,
        )

    except Exception as exc:
        set_outputs_idle()
        return "ERR FIRE {}".format(exc)
    finally:
        set_outputs_idle()
        busy = False


def handle_set(tokens):
    if len(tokens) < 4:
        return "ERR SET"

    view = normalize_view(tokens[1])
    if view is None:
        return "ERR VIEW"

    field = tokens[2].upper()
    value = tokens[3]

    if field == "MODE":
        value = str(value).upper()
        if value not in ("MASTER", "TRIGGER"):
            return "ERR MODE"
        config[view + "_MODE"] = value
        return "OK SET {} MODE {}".format(view, value)

    if field == "DELAY":
        config[view + "_DELAY"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_DELAY"],
            0,
            60000,
        )
        return "OK SET {} DELAY {}".format(
            view,
            config[view + "_DELAY"],
        )

    if field == "PULSE":
        config[view + "_PULSE"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_PULSE"],
            1,
            60000,
        )
        return "OK SET {} PULSE {}".format(
            view,
            config[view + "_PULSE"],
        )

    if field in ("CAPTURE", "CAPTURE_DELAY"):
        config[view + "_CAPTURE"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_CAPTURE"],
            0,
            60000,
        )
        return "OK SET {} CAPTURE {}".format(
            view,
            config[view + "_CAPTURE"],
        )

    if field in ("TRIG", "TRIGGER", "TRIGGER_PULSE"):
        config[view + "_TRIG"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_TRIG"],
            1,
            1000,
        )
        return "OK SET {} TRIG {}".format(
            view,
            config[view + "_TRIG"],
        )

    if field in ("GAP", "TRIG_GAP", "TRIGGER_GAP"):
        config[view + "_GAP"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_GAP"],
            1,
            60000,
        )
        return "OK SET {} GAP {}".format(
            view,
            config[view + "_GAP"],
        )

    if field in ("COUNT", "PULSES", "TRIGGER_COUNT"):
        config[view + "_COUNT"] = clamp_int(
            value,
            DEFAULT_CONFIG[view + "_COUNT"],
            1,
            10,
        )
        return "OK SET {} COUNT {}".format(
            view,
            config[view + "_COUNT"],
        )

    if field == "SINGLE":
        config[view + "_COUNT"] = 1
        return "OK SET {} COUNT 1".format(view)

    if field == "DOUBLE":
        config[view + "_COUNT"] = 2
        return "OK SET {} COUNT 2".format(view)

    return "ERR SET"


def handle_map(tokens):
    if len(tokens) == 3 and tokens[1] == "ALL" and tokens[2] == "OFF":
        for input_name in INPUT_PINS:
            config[input_name] = "OFF"
        return "OK MAP ALL OFF"

    if len(tokens) == 3 and tokens[1] in INPUT_PINS and tokens[2] in ("OFF", "V1", "V2"):
        config[tokens[1]] = tokens[2]
        return "OK MAP {} {}".format(tokens[1], tokens[2])

    return "ERR MAP"


def handle_command(line):
    command = line.strip().upper()
    if not command:
        return []

    tokens = command.split()

    if command == "STATUS":
        return status_lines()

    if command == "SAVE":
        return [save_config()]

    if command == "INPUTS":
        return inputs_lines()

    if command == "LIGHT ON":
        return [set_manual_light(True)]

    if command == "LIGHT OFF":
        return [set_manual_light(False)]

    if command == "LIGHT STATUS":
        return ["MANUAL_LIGHT {}".format("ON" if manual_light_enabled else "OFF")]

    if len(tokens) == 2 and tokens[0] == "FIRE":
        view = normalize_view(tokens[1])
        if view is None:
            return ["ERR VIEW"]
        # Direct profile request for sequential software triggering. In MASTER
        # it emits CAPTURE V1/V2 at the configured production timing.
        return [fire_view(view, capture_source=view)]

    if len(tokens) == 2 and tokens[0] in ("TRIGGER", "SIM"):
        # TRIGGER INx is the software request for the configured explicit
        # source. SIM remains only as a compatibility alias.
        input_name = normalize_input_name(tokens[1])
        if input_name is None:
            return ["ERR INPUT"]

        mapped_view = config.get(input_name, "OFF")
        if mapped_view not in ("V1", "V2"):
            return ["ERR INPUT {} NOT_MAPPED".format(input_name)]

        return [fire_view(mapped_view, capture_source=input_name)]

    if tokens[0] == "SET" and len(tokens) >= 4:
        return [handle_set(tokens)]

    if tokens[0] == "MAP":
        return [handle_map(tokens)]

    return ["ERR UNKNOWN"]


def print_response(response):
    if isinstance(response, str):
        print(response)
    else:
        for line in response:
            print(line)


def poll_inputs():
    debounce_ms = config.get("DEBOUNCE_MS", 30)

    # Edge polling compatible with existing firmware behavior.
    for input_name, pin in inputs.items():
        active = pin.value() == 0
        was_active = previous_input_state.get(input_name, False)
        previous_input_state[input_name] = active

        mapped_view = config.get(input_name, "OFF")

        if active and not was_active and mapped_view in ("V1", "V2"):
            sleep_ms(debounce_ms)

            if pin.value() == 0:
                # The physical input name is passed all the way into fire_view().
                # In MASTER mode this produces "CAPTURE INx".
                # In TRIGGER mode behavior stays equivalent to v3.1.
                print(fire_view(mapped_view, capture_source=input_name))


def setup():
    global config

    config = load_config()
    set_outputs_idle()

    for input_name, gpio in INPUT_PINS.items():
        pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
        inputs[input_name] = pin
        previous_input_state[input_name] = pin.value() == 0

    print(
        "READY {} {} LED=GP{} TRIG=GP{}".format(
            FIRMWARE_NAME,
            FIRMWARE_VERSION,
            LED_PIN,
            CAM_TRIG_PIN,
        )
    )


def main():
    setup()

    while True:
        if stdin_poll.poll(0):
            line = sys.stdin.readline()
            if line:
                print_response(handle_command(line))

        poll_inputs()
        time.sleep_ms(5)


main()
