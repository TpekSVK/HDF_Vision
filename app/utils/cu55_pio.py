"""CU55M Y8 timing, derived from the September 2026 hardware investigation.

All times are integer microseconds. Period means rising edge to rising edge.
"""
from dataclasses import dataclass

CU55_EXPOSURES_US = (500, 1000, 2000, 5000, 10000, 15000, 16000)


@dataclass(frozen=True)
class PioProfile:
    period_us: int
    exposure_us: int
    pulse_us: int = 100

    @property
    def light_lead_us(self):
        return max(2000, self.exposure_us + 1000)

    @property
    def light_duration_us(self):
        return self.light_lead_us + self.period_us + 2000

    @property
    def pre_us(self):
        return self.light_lead_us - self.period_us


def cu55_pio_profile(width, height, fps, pixel_format, exposure_us):
    modes = {(2592, 1944): (30, 33340), (1920, 1080): (60, 16670),
             (1280, 720): (60, 16670), (640, 480): (112, 8930)}
    key = (int(width), int(height))
    if str(pixel_format).upper() not in {"Y8", "GREY", "GRAY8"} or key not in modes:
        raise ValueError("Pico PIO TRIGGER podporuje overené rozlíšenia CU55M vo formáte Y8.")
    expected_fps, period = modes[key]
    if int(fps) != expected_fps:
        raise ValueError(f"Pre toto rozlíšenie nastavte {expected_fps} fps.")
    exposure = int(exposure_us)
    if not 500 <= exposure <= 16000 or exposure % 100:
        raise ValueError("PIO expozícia musí byť 0,5–16 ms v krokoch 0,1 ms.")
    if exposure > period:
        period = exposure + 100
    return PioProfile(period, exposure)
