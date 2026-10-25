"""Auto-detection of the serial port a SIM800C modem is plugged into.

Detection strategy
------------------
1. Enumerate the serial ports exposed by the OS (``serial.tools.list_ports``).
2. Score every port by its USB signature:
   * SIMCom / SIM800 identification in description, manufacturer or hwid,
   * known USB-UART bridges commonly used by SIM800C breakout boards
     (Silicon Labs CP210x, WCH CH340/CH341, FTDI FT232, Prolific PL2303).
3. If the best candidate is confident enough (``MATCH_THRESHOLD``) it is
   returned immediately without touching the port.
4. Otherwise the remaining candidates are probed: the port is opened at the
   configured baudrate and an ``AT`` command is sent — the port answering
   ``OK`` is the modem.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: Values of ``SIM800C_PORT`` that request automatic detection.
AUTO_TOKENS = {"", "auto", "detect", "any"}

#: Minimum score for a port to be accepted without probing.
MATCH_THRESHOLD = 50

#: Seconds to wait for an ``AT`` reply while probing a port.
PROBE_TIMEOUT = 1.5

#: Maximum number of ports probed before giving up.
MAX_PROBES = 6

#: USB-UART bridges used by the common SIM800C development boards.
_KNOWN_BRIDGES: dict[tuple[str, str], str] = {
    ("10C4", "EA60"): "Silicon Labs CP210x",
    ("1A86", "7523"): "WCH CH340",
    ("1A86", "5523"): "WCH CH341",
    ("0403", "6001"): "FTDI FT232",
    ("067B", "2303"): "Prolific PL2303",
}
_BRIDGE_VIDS = {vid for vid, _ in _KNOWN_BRIDGES}

#: Strings that identify the modem itself (scored highest).
_KEYWORDS = ("sim800", "sim8", "simcom", "sim-com", "800c", "gsm modem")


class PortDetectionError(Exception):
    """Raised when no SIM800C-compatible serial port can be found."""


@dataclass
class PortCandidate:
    device: str
    description: str = ""
    hwid: str = ""
    vid: str = ""
    pid: str = ""
    manufacturer: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return self.score >= MATCH_THRESHOLD

    @property
    def reason(self) -> str:
        return ", ".join(self.reasons) if self.reasons else ""


def is_auto(value: Optional[str]) -> bool:
    """True when *value* asks for automatic port detection."""
    return (value or "").strip().lower() in AUTO_TOKENS


def scan_ports() -> list[PortCandidate]:
    """Return every serial port, scored and sorted by SIM800C likelihood."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - packaging issue
        raise PortDetectionError("pyserial is not installed") from exc

    candidates: list[PortCandidate] = []
    for info in list_ports.comports():
        candidate = PortCandidate(
            device=info.device,
            description=info.description or "",
            hwid=info.hwid or "",
            vid=f"{info.vid:04X}" if info.vid is not None else "",
            pid=f"{info.pid:04X}" if info.pid is not None else "",
            manufacturer=(info.manufacturer or "").strip(),
        )
        _score(candidate)
        candidates.append(candidate)

    candidates.sort(key=lambda c: (c.score, c.device), reverse=True)
    return candidates


def _score(candidate: PortCandidate) -> None:
    haystack = " ".join(
        (candidate.device, candidate.description, candidate.hwid, candidate.manufacturer)
    ).lower()

    if any(word in haystack for word in _KEYWORDS):
        candidate.score += 80
        candidate.reasons.append("SIM800/SIMCom identifier found")

    if "simcom" in candidate.manufacturer.lower():
        candidate.score += 40
        candidate.reasons.append("manufactured by SIMCom")

    bridge_key = (candidate.vid, candidate.pid)
    if candidate.vid and candidate.pid and bridge_key in _KNOWN_BRIDGES:
        candidate.score += 40
        candidate.reasons.append(f"{_KNOWN_BRIDGES[bridge_key]} USB-UART bridge")
    elif candidate.vid in _BRIDGE_VIDS:
        candidate.score += 15
        candidate.reasons.append("common USB-UART bridge")


def _probe(candidate: PortCandidate, baudrate: int) -> bool:
    """Open *candidate* briefly and check whether it answers ``AT``."""
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - packaging issue
        raise PortDetectionError("pyserial is not installed") from exc

    try:
        with serial.Serial(
            port=candidate.device,
            baudrate=baudrate,
            timeout=PROBE_TIMEOUT,
            write_timeout=PROBE_TIMEOUT,
        ) as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"AT\r")
            ser.flush()
            deadline = time.time() + PROBE_TIMEOUT
            buffer = b""
            while time.time() < deadline:
                waiting = ser.in_waiting
                if waiting:
                    buffer += ser.read(waiting)
                    if b"OK" in buffer:
                        return True
                else:
                    time.sleep(0.05)
    except Exception as exc:  # noqa: BLE001 - port busy, denied, or unplugged
        logger.debug("Probe of %s failed: %s", candidate.device, exc)
        return False

    logger.debug("No AT response from %s", candidate.device)
    return False


def detect_port(
    baudrate: int = 115200,
    probe: bool = True,
    exclude: Iterable[str] = (),
) -> tuple[PortCandidate, str]:
    """Find the port the SIM800C is plugged into.

    Returns ``(candidate, method)`` where *method* is ``"signature"`` when the
    USB identity was unambiguous or ``"probe"`` when an ``AT`` probe confirmed
    the port.

    Raises:
        PortDetectionError: when no compatible port can be found.
    """
    candidates = scan_ports()
    if not candidates:
        raise PortDetectionError(
            "No serial ports found. Plug in the SIM800C and check its driver."
        )

    skipped = {device for device in exclude if device}
    usable = [c for c in candidates if c.device not in skipped]
    if not usable:
        usable = candidates

    # 1. Unambiguous USB signature — no need to touch the port.
    for candidate in usable:
        if candidate.is_match:
            return candidate, "signature"

    # 2. Probe likely candidates first, then whatever is left (bounded).
    if probe:
        ordered = sorted(usable, key=lambda c: c.score, reverse=True)
        probed = 0
        for candidate in ordered:
            if probed >= MAX_PROBES:
                break
            probed += 1
            logger.debug(
                "Probing serial port %s (score %s)", candidate.device, candidate.score
            )
            if _probe(candidate, baudrate):
                if not candidate.reasons:
                    candidate.reasons.append("answered AT probe")
                return candidate, "probe"

    available = ", ".join(c.device for c in candidates)
    raise PortDetectionError(
        f"No SIM800C detected among the available serial ports ({available}). "
        "Set SIM800C_PORT manually to force a port."
    )
