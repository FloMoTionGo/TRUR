"""Phone test devices (docs/test-strategy.md §7, "Devices"). Pick a set with --devices=core|full|id,id.

User agents come from Playwright's descriptors; the app never reads them. `land` joins `core` with P14,
and until then the phone cases run on the portrait devices only.
"""

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Device:
    id: str
    width: int
    height: int
    dpr: float
    touch: bool
    core: bool
    descriptor: str | None  # Playwright device whose user agent this context borrows

    @property
    def portrait(self) -> bool:
        return self.touch and self.height > self.width

    def context_args(self, playwright) -> dict:
        args = {
            "viewport": {"width": self.width, "height": self.height},
            "device_scale_factor": self.dpr,
            "is_mobile": self.touch,
            "has_touch": self.touch,
            "timezone_id": "UTC",
            "locale": "en-US",
            "color_scheme": "light",
        }
        if self.descriptor:
            args["user_agent"] = playwright.devices[self.descriptor]["user_agent"]
        return args


DEVICES = [
    Device("env-corner", 360, 550, 3, True, True, "Galaxy S24"),        # design-envelope corner
    Device("s360", 360, 780, 3, True, False, "Galaxy S24"),
    Device("w320", 320, 568, 2, True, False, "iPhone SE"),             # reflow floor
    Device("se", 375, 667, 2, True, False, "iPhone SE (3rd gen)"),
    Device("se-bars", 375, 553, 2, True, True, "iPhone SE (3rd gen)"),  # worst case
    Device("ip17-bars", 402, 681, 3, True, False, "iPhone 17"),
    Device("px", 412, 839, 2.625, True, False, "Pixel 7"),
    Device("promax", 440, 763, 3, True, False, "iPhone 17 Pro Max"),
    Device("land", 874, 402, 3, True, False, "iPhone 17 landscape"),   # core from P14
    Device("land-short", 756, 308, 2.625, True, False, "Pixel 10 landscape"),
    Device("desk-short", 1280, 600, 1, False, False, None),            # short desktop window, mouse
]
BY_ID = {d.id: d for d in DEVICES}


def selected(config: pytest.Config) -> list[Device]:
    choice = config.getoption("devices")
    if choice == "core":
        return [d for d in DEVICES if d.core]
    if choice == "full":
        return list(DEVICES)
    unknown = [i for i in choice.split(",") if i not in BY_ID]
    if unknown:
        raise pytest.UsageError(f"--devices: unknown device {', '.join(unknown)}; known: {', '.join(BY_ID)}")
    return [BY_ID[i] for i in choice.split(",")]
