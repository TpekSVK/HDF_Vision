"""HDF Vision Raspberry Pi Pico controller firmware v3.2.0.

MicroPython firmware for LED/light and See3CAM_CU55M trigger control.
Copy this file to the Pico as main.py.

Main changes vs v3.1.0:
- MASTER mode can emit asynchronous USB capture events: CAPTURE IN1 ... IN8
- capture event always reports the physical Pico input that caused the cycle
- new per-view CAPTURE delay controls event timing after LIGHT ON
- no event numbering is used
- existing TRIGGER mode and V1/V2 mapping remain supported
"""

import json
import select
import sys
import time

from machine import Pin

FIRMWARE_NAME = "pico_hdf_controller"
FIRMWARE_VERSION = "3.2.0-master-capture"
CONFIG_FILE = "hdf_pico_config.json"

LED_PIN = 17
CAM_TRIG_PIN = 16
INPUT_PINS = {"IN{}".format(index): index for index in range(1, 9)}

DEFAULT_CONFIG = {
    "V1_MODE": "TRIGGER", "V2_MODE": "TRIGGER",
    "V1_DELAY": 0, "V2_DELAY": 0,
    "V1_PULSE": 200, "V2_PULSE": 200,
    "V1_CAPTURE": 5, "V2_CAPTURE": 5,
    "V1_TRIG": 10, "V2_TRIG": 10,
    "V1_GAP": 300, "V2_GAP": 300,
    "V1_COUNT": 2, "V2_COUNT": 2,
    "IN1": "OFF", "IN2": "OFF", "IN3": "OFF", "IN4": "OFF",
    "IN5": "OFF", "IN6": "OFF", "IN7": "OFF", "IN8": "OFF",
    "DEBOUNCE_MS": 30, "LOCKOUT_MS": 100,
}

led = Pin(LED_PIN, Pin.OUT)
cam_trig = Pin(CAM_TRIG_PIN, Pin.OUT)
inputs = {}
config = {}
busy = False
busy_until_ms = 0
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
    return min(max(iv, min_value), max_value)


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
        limits = {
            "DELAY": (0, 60000), "PULSE": (1, 60000),
            "CAPTURE": (0, 60000), "TRIG": (1, 1000),
            "GAP": (1, 60000), "COUNT": (1, 10),
        }
        for field, bounds in limits.items():
            key = view + "_" + field
            cfg[key] = clamp_int(cfg.get(key), DEFAULT_CONFIG[key], bounds[0], bounds[1])
    for input_name in INPUT_PINS:
        value = str(cfg.get(input_name, "OFF")).upper()
        cfg[input_name] = value if value in ("OFF", "V1", "V2") else "OFF"
    cfg["DEBOUNCE_MS"] = clamp_int(cfg.get("DEBOUNCE_MS"), 30, 1, 1000)
    cfg["LOCKOUT_MS"] = clamp_int(cfg.get("LOCKOUT_MS"), 100, 0, 60000)
    return cfg


def merge_defaults(saved):
    merged = DEFAULT_CONFIG.copy()
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in merged:
                merged[key] = value
        views = saved.get("views") if isinstance(saved.get("views"), dict) else None
        if views:
            for view in ("V1", "V2"):
                view_cfg = views.get(view, {}) if isinstance(views.get(view), dict) else {}
                mapping = {
                    "delay_ms": view + "_DELAY", "pulse_ms": view + "_PULSE",
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
                if str(idx) in input_map:
                    merged["IN{}".format(idx)] = str(input_map[str(idx)]).upper()
    return normalize_config(merged)


def load_config():
    try:
        with open(CONFIG_FILE, "r") as handle:
            return merge_defaults(json.load(handle))
    except (OSError, ValueError):
        return merge_defaults({})


def save_config():
    with open(CONFIG_FILE, "w") as handle:
        json.dump(config, handle)
    return "OK SAVED"


def input_mapping_summary():
    return " ".join(name + "=" + config.get(name, "OFF") for name in sorted(INPUT_PINS))


def status_lines():
    lines = ["FIRMWARE {} {}".format(FIRMWARE_NAME, FIRMWARE_VERSION),
             "PINS LED=GP{} TRIG=GP{}".format(LED_PIN, CAM_TRIG_PIN)]
    for view in ("V1", "V2"):
        for field in ("MODE", "DELAY", "PULSE", "CAPTURE", "TRIG", "GAP", "COUNT"):
            lines.append("{}_{} {}".format(view, field, config[view + "_" + field]))
    lines.extend(["INPUT_MAP " + input_mapping_summary(),
                  "DEBOUNCE_MS {}".format(config["DEBOUNCE_MS"]),
                  "LOCKOUT_MS {}".format(config["LOCKOUT_MS"]),
                  "NOTE MASTER physical input emits CAPTURE INx",
                  "NOTE COUNT=2 means trigger #1 dummy, trigger #2 capture", "END"])
    return lines


def inputs_lines():
    parts = ["{}={}".format(name, "ACTIVE" if inputs[name].value() == 0 else "OFF")
             for name in sorted(inputs)]
    return ["INPUTS " + " ".join(parts), "END"]


def set_outputs_idle():
    led.value(0)
    cam_trig.value(0)


def trigger_pulse(trig_ms):
    cam_trig.value(1)
    sleep_ms(trig_ms)
    cam_trig.value(0)


def emit_capture_event(input_name):
    input_name = normalize_input_name(input_name)
    if input_name is None:
        return False
    print("CAPTURE {}".format(input_name))
    return True


def fire_view(view, source_input=None):
    global busy, busy_until_ms
    view = normalize_view(view)
    if view is None:
        return "ERR VIEW"
    source_input = normalize_input_name(source_input)
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
        if mode == "TRIGGER":
            active_ms = max(pulse_ms, trig_ms * count + gap_ms * max(0, count - 1))
        else:
            active_ms = max(pulse_ms, capture_ms)
        busy_until_ms = time.ticks_add(now, delay_ms + active_ms + config["LOCKOUT_MS"])
        sleep_ms(delay_ms)
        led.value(1)
        light_on_started = now_ms()
        if mode == "TRIGGER":
            for index in range(count):
                trigger_pulse(trig_ms)
                if index < count - 1:
                    sleep_ms(gap_ms)
        else:
            sleep_ms(capture_ms)
            if source_input is not None:
                emit_capture_event(source_input)
        sleep_ms(pulse_ms - diff_ms(now_ms(), light_on_started))
        set_outputs_idle()
        if mode == "MASTER":
            note = "MASTER_CAPTURE" if source_input is not None else "MASTER_MANUAL_FIRE"
        else:
            note = "DOUBLE_DUMMY_CAPTURE" if count >= 2 else "SINGLE"
        return ("OK FIRED {} MODE={} SOURCE={} DELAY={} PULSE={} CAPTURE={} "
                "TRIG={} GAP={} COUNT={} NOTE={}").format(
                    view, mode, source_input or "USB", delay_ms, pulse_ms,
                    capture_ms, trig_ms, gap_ms, count, note)
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
    field, value = tokens[2].upper(), tokens[3]
    if field == "MODE":
        value = str(value).upper()
        if value not in ("MASTER", "TRIGGER"):
            return "ERR MODE"
        config[view + "_MODE"] = value
        return "OK SET {} MODE {}".format(view, value)
    aliases = {
        "DELAY": ("DELAY", 0, 60000), "PULSE": ("PULSE", 1, 60000),
        "CAPTURE": ("CAPTURE", 0, 60000), "CAPTURE_DELAY": ("CAPTURE", 0, 60000),
        "TRIG": ("TRIG", 1, 1000), "TRIGGER": ("TRIG", 1, 1000),
        "TRIGGER_PULSE": ("TRIG", 1, 1000), "GAP": ("GAP", 1, 60000),
        "TRIG_GAP": ("GAP", 1, 60000), "TRIGGER_GAP": ("GAP", 1, 60000),
        "COUNT": ("COUNT", 1, 10), "PULSES": ("COUNT", 1, 10),
        "TRIGGER_COUNT": ("COUNT", 1, 10),
    }
    if field in aliases:
        canonical, minimum, maximum = aliases[field]
        key = view + "_" + canonical
        config[key] = clamp_int(value, DEFAULT_CONFIG[key], minimum, maximum)
        return "OK SET {} {} {}".format(view, canonical, config[key])
    if field in ("SINGLE", "DOUBLE"):
        config[view + "_COUNT"] = 1 if field == "SINGLE" else 2
        return "OK SET {} COUNT {}".format(view, config[view + "_COUNT"])
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
    if len(tokens) == 2 and tokens[0] == "FIRE":
        return [fire_view(tokens[1], source_input=None)]
    if tokens[0] == "SET" and len(tokens) >= 4:
        return [handle_set(tokens)]
    if tokens[0] == "MAP":
        return [handle_map(tokens)]
    return ["ERR UNKNOWN"]


def print_response(response):
    for line in ([response] if isinstance(response, str) else response):
        print(line)


def poll_inputs():
    for input_name, pin in inputs.items():
        active = pin.value() == 0
        was_active = previous_input_state.get(input_name, False)
        previous_input_state[input_name] = active
        mapped_view = config.get(input_name, "OFF")
        if active and not was_active and mapped_view in ("V1", "V2"):
            sleep_ms(config.get("DEBOUNCE_MS", 30))
            if pin.value() == 0:
                print(fire_view(mapped_view, source_input=input_name))


def setup():
    global config
    config = load_config()
    set_outputs_idle()
    for input_name, gpio in INPUT_PINS.items():
        pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
        inputs[input_name] = pin
        previous_input_state[input_name] = pin.value() == 0
    print("READY {} {} LED=GP{} TRIG=GP{}".format(
        FIRMWARE_NAME, FIRMWARE_VERSION, LED_PIN, CAM_TRIG_PIN))


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
