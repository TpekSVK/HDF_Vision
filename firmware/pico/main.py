# HDF Vision Pico 4.3 — independently scheduled camera channels.
"""Single-file MicroPython firmware for the dual-output Pico/Pico2 board.

CH 1 <command> addresses GP16/17; CH 2 <command> addresses GP18/19.
Every reply/event is prefixed CH n for channel-addressed clients. Unprefixed
commands retain channel-1 compatibility. Finite PIO trains and MASTER windows
are serviced without sleeping through another channel's acquisition.
"""
import json
import select
import sys
import time
import rp2
from machine import Pin

FIRMWARE_NAME = 'pico_hdf_controller'
FIRMWARE_VERSION = '4.3.1-dual-channel'
CONFIG_FILE = 'hdf_pico_config.json'
INPUT_PINS = {'IN' + str(i): i for i in range(1, 9)}
DEFAULT_CONFIG = {'DEBOUNCE_MS': 30, 'LOCKOUT_MS': 100}
for _profile in ('V1', 'V2'):
    for _field, _value in (('MODE', 'TRIGGER'), ('DELAY', 0), ('PULSE', 200),
                           ('CAPTURE', 5), ('TRIG', 10), ('GAP', 300), ('COUNT', 2)):
        DEFAULT_CONFIG[_profile + '_' + _field] = _value
for _input in INPUT_PINS:
    DEFAULT_CONFIG[_input] = 'OFF'


@rp2.asm_pio(out_init=(rp2.PIO.OUT_LOW,) * 2, out_shiftdir=rp2.PIO.SHIFT_RIGHT,
             fifo_join=rp2.PIO.JOIN_TX)
def pio_events():
    pull(block)
    out(pins, 2)
    out(x, 30)
    label('hold')
    jmp(x_dec, 'hold')


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_RIGHT)
def live_trigger_program():
    pull(block)
    mov(isr, osr)
    label('pulse')
    set(pins, 1)
    mov(osr, isr)
    out(y, 16)
    label('high')
    jmp(y_dec, 'high')
    set(pins, 0)
    out(y, 16)
    label('low')
    jmp(y_dec, 'low')
    jmp('pulse')


def normalize_config(raw):
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg
    ranges = {'DELAY': (0, 60000), 'PULSE': (1, 60000), 'CAPTURE': (0, 60000),
              'TRIG': (1, 1000), 'GAP': (1, 60000), 'COUNT': (1, 10),
              'DEBOUNCE_MS': (1, 1000), 'LOCKOUT_MS': (0, 60000)}
    for key in cfg:
        value = raw.get(key, cfg[key])
        if key.startswith('IN'):
            cfg[key] = value if value in ('OFF', 'V1', 'V2') else 'OFF'
        elif key.endswith('_MODE'):
            cfg[key] = value if value in ('MASTER', 'TRIGGER') else 'TRIGGER'
        else:
            field = key if key.endswith('_MS') else key.split('_', 1)[1]
            lower, upper = ranges[field]
            try:
                cfg[key] = max(lower, min(upper, int(value)))
            except (ValueError, TypeError):
                pass
    return cfg


class Channel:
    def __init__(self, number, cfg):
        self.number = number
        self.trigger_pin = 16 + (number - 1) * 2
        self.trigger = Pin(self.trigger_pin, Pin.OUT, value=0)
        self.light = Pin(self.trigger_pin + 1, Pin.OUT, value=0)
        self.config = normalize_config(cfg)
        self.mode = 'IDLE'
        self.tagged = number == 2
        self.manual_light = False
        self.sm = None
        self.live = False
        self.job = None
        self.last_input_ms = {}

    def emit(self, line):
        print(('CH %d ' % self.number if self.tagged else '') + line)

    def low(self):
        if self.sm is not None:
            self.sm.active(0)
        self.sm = None
        self.live = False
        self.manual_light = False
        self.trigger.init(Pin.OUT, value=0)
        self.light.init(Pin.OUT, value=0)

    def status(self):
        lines = ['FIRMWARE %s %s' % (FIRMWARE_NAME, FIRMWARE_VERSION),
                 'CAPABILITIES PIO_PAIR_V1 SESSION_V1 LIVE_PIO_V1 DUAL_CHANNEL_V1 HOST_INPUT_V1',
                 'CHANNEL %d' % self.number,
                 'PINS LED=GP%d TRIG=GP%d' % (self.trigger_pin + 1, self.trigger_pin),
                 'SESSION ' + self.mode, 'SESSION_MODE ' + self.mode,
                 'BUSY ' + ('ON' if self.job else 'OFF'),
                 'LIVE ' + ('ON' if self.live else 'OFF'),
                 'MANUAL_LIGHT ' + ('ON' if self.manual_light else 'OFF')]
        lines.extend('%s %s' % item for item in self.config.items())
        lines.append('INPUT_MAP ' + ' '.join('%s=%s' % (name, self.config[name]) for name in INPUT_PINS))
        return lines + ['END']

    def fire_pio(self, tokens):
        if self.job or self.live:
            return ['BUSY PIO']
        if self.mode == 'MASTER':
            return ['ERR PIO SESSION_' + self.mode]
        if len(tokens) != 10:
            return ['ERR PIO ARGUMENTS']
        try:
            ident, count, period, pulse, pre, duration, enabled, tail = [int(v) for v in tokens[2:]]
        except ValueError:
            return ['ERR PIO ARGUMENTS']
        if not (1 <= count <= 3 and 8930 <= period <= 100000 and 10 <= pulse < period
                and -100000 <= pre <= 100000 and 0 <= duration <= 200000
                and enabled in (0, 1) and 20000 <= tail <= 100000):
            return ['ERR PIO RANGE']
        trigger_start = 2000 + max(pre, 0)
        light_start = 2000 + max(-pre, 0)
        events = []
        for i in range(count):
            events.extend([(trigger_start + i * period, 1, 1, i),
                           (trigger_start + i * period + pulse, 1, 0, i)])
        if enabled and duration:
            events.extend([(light_start, 0, 1, -1), (light_start + duration, 0, 0, -1)])
        events.sort()
        end = max(events[-1][0] + 10, trigger_start + (count - 1) * period + tail)
        states, state, stamps = [(0, 0)], 0, []
        for stamp, output, value, index in events:
            bit = 1 if output else 2
            state = state | bit if value else state & ~bit
            if states[-1][0] == stamp:
                states[-1] = (stamp, state)
            else:
                states.append((stamp, state))
            stamps.append([output, value, index, stamp - 2000, stamp - 2000, stamp - 2000])
        states.append((end, 0))
        words = []
        for i, (stamp, state) in enumerate(states):
            delay = states[i + 1][0] - stamp if i + 1 < len(states) else 10
            if delay < 4:
                return ['ERR PIO EVENTS TOO CLOSE']
            words.append(((delay - 4) << 2) | state)
        self.low()
        try:
            self.sm = rp2.StateMachine(self.number - 1, pio_events, freq=1000000,
                                       out_base=Pin(self.trigger_pin))
            for word in words[:8]:
                self.sm.put(word)
            start = time.ticks_us()
            self.job = dict(kind='pio', start=start, end=end + 10000,
                            words=words, cursor=min(8, len(words)),
                            result={'id': ident, 'channel': self.number, 'count': count,
                                    'period_us': period, 'pulse_us': pulse,
                                    'light_pre_us': pre, 'light_duration_us': duration,
                                    'light_enable': enabled, 'events': stamps,
                                    'start_ticks_us': start, 'pio_words': words,
                                    'timing_basis': 'PIO programmed cycles at 1MHz; not measured electrical edges'})
            self.sm.active(1)
            return []  # reply when this channel's hardware train finishes
        except Exception as exc:
            self.low()
            self.job = None
            return ['ERR PIO ' + str(exc)]

    def fire(self, profile, source):
        if self.mode == 'IDLE':
            return ['ERR SESSION_IDLE']
        if self.job or self.live:
            return ['BUSY ' + source]
        if self.mode == 'TRIGGER':
            self.emit('REQUEST ' + source)
            return ['OK REQUESTED ' + source]
        delay = self.config[profile + '_DELAY']
        pulse = self.config[profile + '_PULSE']
        capture = self.config[profile + '_CAPTURE']
        if capture > pulse:
            return ['ERR CAPTURE_AFTER_LIGHT']
        self.low()
        self.job = dict(kind='master', start=time.ticks_us(), source=source,
                        delay=delay * 1000, capture=(delay + capture) * 1000,
                        end=(delay + pulse) * 1000, light_on=False, emitted=False)
        return []

    def request_input(self, profile, source):
        if self.tagged:
            # The host selects the view/profile and reserves the camera before
            # starting its illumination window. No image can arrive against an
            # old profile while another view is being selected in the UI.
            if self.mode == 'IDLE':
                return ['ERR SESSION_IDLE']
            if self.job or self.live:
                return ['BUSY ' + source]
            self.emit('REQUEST ' + source)
            return []
        return self.fire(profile, source)

    def tick(self):
        job = self.job
        if job is None:
            return
        elapsed = time.ticks_diff(time.ticks_us(), job['start'])
        if job['kind'] == 'pio':
            while job['cursor'] < len(job['words']) and self.sm.tx_fifo() < 8:
                self.sm.put(job['words'][job['cursor']])
                job['cursor'] += 1
            if elapsed >= job['end']:
                result = job['result']
                result['elapsed_us'] = elapsed
                self.low()
                self.job = None
                self.emit('PIO RESULT ' + json.dumps(result))
                self.emit('OK PIO ' + str(result['id']))
        else:
            if not job['light_on'] and elapsed >= job['delay']:
                self.light.value(1)
                job['light_on'] = True
            if not job['emitted'] and elapsed >= job['capture']:
                self.emit('CAPTURE ' + job['source'])
                job['emitted'] = True
            if elapsed >= job['end']:
                self.low()
                self.job = None
                self.emit('OK FIRED ' + job['source'])

    def handle(self, command):
        tokens = command.upper().split()
        if not tokens:
            return []
        if tokens == ['STATUS']:
            return self.status()
        if tokens == ['INPUTS']:
            return ['INPUTS ' + ' '.join('%s=%s' % (name, 'ACTIVE' if pin.value() == 0 else 'OFF')
                                         for name, pin in inputs.items()), 'END']
        if tokens == ['SAVE']:
            return [save_config()]
        if tokens[0] == 'SESSION' and len(tokens) == 2:
            if tokens[1] not in ('IDLE', 'MASTER', 'TRIGGER'):
                return ['ERR SESSION MODE']
            if self.job:
                return ['BUSY SESSION']
            self.low()
            self.mode = tokens[1]
            return ['OK SESSION ' + self.mode]
        if tokens[:2] == ['PIO', 'FIRE']:
            return self.fire_pio(tokens)
        if tokens[:1] == ['LIVE']:
            if tokens == ['LIVE', 'OFF']:
                if self.job:
                    return ['BUSY LIVE']
                if self.sm:
                    self.sm.active(0)
                self.sm = None
                self.live = False
                self.trigger.init(Pin.OUT, value=0)
                return ['OK LIVE OFF']
            if self.mode != 'TRIGGER' or self.job or len(tokens) not in (2, 4) or tokens[1] != 'ON':
                return ['ERR LIVE SESSION']
            try:
                period, pulse = (33340, 100) if len(tokens) == 2 else (int(tokens[2]), int(tokens[3]))
                if not (8930 <= period <= 65535 and 10 <= pulse <= 1000 and pulse < period - 4):
                    return ['ERR LIVE RANGE']
                if self.sm:
                    self.sm.active(0)
                self.sm = rp2.StateMachine(self.number - 1, live_trigger_program,
                                           freq=1000000, set_base=Pin(self.trigger_pin))
                self.sm.put((pulse - 4) | ((period - pulse - 4) << 16))
                self.live = True
                self.sm.active(1)
                return ['OK ' + ' '.join(tokens)]
            except Exception as exc:
                self.low()
                return ['ERR LIVE ' + str(exc)]
        if tokens[:1] == ['LIGHT']:
            if tokens == ['LIGHT', 'STATUS']:
                return ['MANUAL_LIGHT ' + ('ON' if self.manual_light else 'OFF')]
            if len(tokens) != 2 or tokens[1] not in ('ON', 'OFF'):
                return ['ERR LIGHT ARGUMENTS']
            if tokens[1] == 'ON' and (self.mode == 'IDLE' or self.job):
                return ['ERR LIGHT SESSION_NOT_READY']
            self.manual_light = tokens[1] == 'ON'
            self.light.value(int(self.manual_light))
            return ['OK LIGHT ' + tokens[1]]
        if tokens[0] == 'FIRE' and len(tokens) == 2 and tokens[1] in ('V1', 'V2'):
            return self.fire(tokens[1], tokens[1])
        if tokens[0] in ('TRIGGER', 'SIM') and len(tokens) == 2 and tokens[1] in INPUT_PINS:
            profile = self.config[tokens[1]]
            if profile not in ('V1', 'V2'):
                return ['ERR INPUT ' + tokens[1] + ' NOT_MAPPED']
            return self.fire(profile, tokens[1])
        if tokens[0] == 'MAP' and len(tokens) == 3:
            if tokens[1] not in INPUT_PINS or tokens[2] not in ('OFF', 'V1', 'V2'):
                return ['ERR MAP']
            self.config[tokens[1]] = tokens[2]
            return ['OK ' + ' '.join(tokens)]
        if tokens[0] == 'SET' and len(tokens) in (3, 4):
            key = tokens[1] + '_' + tokens[2] if len(tokens) == 4 else tokens[1]
            if key not in self.config or key in INPUT_PINS:
                return ['ERR SET FIELD']
            raw = tokens[-1]
            if key.endswith('_MODE'):
                if raw not in ('MASTER', 'TRIGGER'):
                    return ['ERR SET MODE']
                value = raw
            else:
                try:
                    value = int(raw)
                except ValueError:
                    return ['ERR SET VALUE']
                candidate = dict(self.config)
                candidate[key] = value
                if normalize_config(candidate)[key] != value:
                    return ['ERR SET RANGE']
            self.config[key] = value
            return ['OK ' + ' '.join(tokens)]
        return ['ERR UNKNOWN']


def load_config():
    try:
        with open(CONFIG_FILE) as stream:
            raw = json.load(stream)
        if not isinstance(raw, dict):
            return {}
        if raw.get('version') == 2 and isinstance(raw.get('channels'), dict):
            return raw['channels']
        return {'1': raw}  # preserve settings of the previous single channel
    except (OSError, ValueError):
        return {}


def save_config():
    try:
        import os
        payload = {'version': 2, 'channels': {str(c.number): c.config for c in channels}}
        with open(CONFIG_FILE + '.tmp', 'w') as stream:
            json.dump(payload, stream)
        os.rename(CONFIG_FILE + '.tmp', CONFIG_FILE)
        return 'OK SAVED'
    except Exception as exc:
        return 'ERR SAVE ' + str(exc)


def handle_command(line):
    tokens = line.strip().split()
    if not tokens:
        return
    channel = channels[0]
    if tokens[0].upper() != 'CH':
        # A later single-camera application/updater can use the legacy protocol
        # after the dual-channel client closes its exclusive serial connection.
        channel.tagged = False
    if tokens[0].upper() == 'CH':
        if len(tokens) < 3 or tokens[1] not in ('1', '2'):
            print('ERR CHANNEL')
            return
        channel = channels[int(tokens[1]) - 1]
        channel.tagged = True
        tokens = tokens[2:]
    try:
        for response in channel.handle(' '.join(tokens)):
            channel.emit(response)
    except Exception as exc:
        channel.low()
        channel.job = None
        channel.emit('ERR COMMAND ' + str(exc))


def poll_inputs():
    now = time.ticks_ms()
    for name, pin in inputs.items():
        active = pin.value() == 0
        state = input_states[name]
        if active != state[0]:
            state[0], state[1] = active, now
        if not active:
            state[2] = False
        if not active or state[2]:
            continue
        # Per-channel dispatch uses stable input levels without blocking sleeps.
        for channel in channels:
            profile = channel.config[name]
            if profile not in ('V1', 'V2'):
                continue
            if time.ticks_diff(now, state[1]) < channel.config['DEBOUNCE_MS']:
                continue
            if channel.last_input_ms.get(name) == state[1]:
                continue
            channel.last_input_ms[name] = state[1]
            previous = channel.last_input_ms.get('last', now - 60000)
            if time.ticks_diff(now, previous) < channel.config['LOCKOUT_MS']:
                continue
            channel.last_input_ms['last'] = now
            for response in channel.request_input(profile, name):
                channel.emit(response)


def main():
    global channels, inputs, input_states
    cfg = load_config()
    channels = [Channel(i, cfg.get(str(i), {})) for i in (1, 2)]
    inputs = {name: Pin(gpio, Pin.IN, Pin.PULL_UP) for name, gpio in INPUT_PINS.items()}
    input_states = {name: [pin.value() == 0, time.ticks_ms(), pin.value() == 0]
                    for name, pin in inputs.items()}
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    print('READY %s %s CHANNELS=2' % (FIRMWARE_NAME, FIRMWARE_VERSION))
    try:
        while True:
            for channel in channels:
                channel.tick()
            if poll.poll(0):
                line = sys.stdin.readline()
                if line:
                    handle_command(line)
            poll_inputs()
            time.sleep_us(200)
    finally:
        for channel in channels:
            channel.low()


main()
