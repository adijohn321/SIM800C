"""SIM800C GSM modem driver using AT commands over serial."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.port_detect import PortDetectionError, detect_port, is_auto

logger = logging.getLogger(__name__)

# Basic GSM 03.38 characters (enough to decide when UCS2 is required)
_GSM_CHARS = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)


@dataclass
class ModemStatus:
    connected: bool = False
    mock: bool = False
    port: str = ""
    signal_rssi: Optional[int] = None
    signal_ber: Optional[int] = None
    network_registered: bool = False
    operator: str = ""
    imei: str = ""
    sim_ready: bool = False
    last_error: str = ""


@dataclass
class RawSms:
    index: int
    status: str
    sender: str
    timestamp: Optional[datetime]
    body: str


class Sim800CError(Exception):
    """Raised when the modem returns an error or times out."""


class Sim800CModem:
    """Thread-safe SIM800C modem wrapper."""

    def __init__(
        self,
        port: str = "auto",
        baudrate: int = 115200,
        timeout: float = 5.0,
        mock: bool = False,
    ) -> None:
        self._auto_port = is_auto(port)
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.mock = mock
        self._ser = None
        self._lock = threading.RLock()
        self.status = ModemStatus(mock=mock, port=port)
        self._mock_inbox: list[RawSms] = []
        self._mock_next_index = 1

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> ModemStatus:
        with self._lock:
            if self.mock:
                return self._connect_mock()
            return self._connect_real()

    def disconnect(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.close()
                except Exception:  # noqa: BLE001
                    pass
            self._ser = None
            self.status.connected = False

    def _connect_mock(self) -> ModemStatus:
        self.status = ModemStatus(
            connected=True,
            mock=True,
            port="MOCK",
            signal_rssi=20,
            signal_ber=0,
            network_registered=True,
            operator="MockNet",
            imei="860000000000001",
            sim_ready=True,
        )
        if not self._mock_inbox:
            self._seed_mock_messages()
        logger.info("Connected to mock SIM800C modem")
        return self.status

    def _connect_real(self) -> ModemStatus:
        if self._auto_port:
            try:
                candidate, method = detect_port(baudrate=self.baudrate)
            except PortDetectionError as exc:
                self.status.connected = False
                self.status.last_error = str(exc)
                raise Sim800CError(str(exc)) from exc
            logger.info(
                "Auto-detected SIM800C on %s via %s detection (%s)",
                candidate.device,
                method,
                candidate.description or "no description",
            )
            self.port = candidate.device
        try:
            import serial

            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
            time.sleep(0.3)
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()

            self._at("AT")
            self._at("ATE0")  # echo off
            self._at("AT+CMGF=1")  # text mode
            self._at('AT+CSCS="GSM"')
            try:
                self._at("AT+CSMP=17,167,0,0")
            except Sim800CError:
                pass
            # New message indication: buffer on module, also notify
            try:
                self._at("AT+CNMI=2,1,0,0,0")
            except Sim800CError:
                pass

            self.refresh_status()
            self.status.connected = True
            self.status.mock = False
            self.status.last_error = ""
            logger.info("Connected to SIM800C on %s", self.port)
            return self.status
        except Exception as exc:  # noqa: BLE001
            self.status.connected = False
            self.status.last_error = str(exc)
            logger.warning("Failed to connect to SIM800C on %s: %s", self.port, exc)
            if self._ser:
                try:
                    self._ser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ser = None
            raise Sim800CError(f"Cannot open modem on {self.port}: {exc}") from exc

    def _seed_mock_messages(self) -> None:
        now = datetime.now(timezone.utc)
        samples = [
            ("+15551234567", "Welcome! Your SIM800C manager is in mock mode."),
            ("+15559876543", "Package delivered. Reply YES to confirm."),
            ("+15551112222", "OTP code: 482913 — valid for 5 minutes."),
        ]
        for sender, body in samples:
            self._mock_inbox.append(
                RawSms(
                    index=self._mock_next_index,
                    status="REC UNREAD",
                    sender=sender,
                    timestamp=now,
                    body=body,
                )
            )
            self._mock_next_index += 1

    # ------------------------------------------------------------------
    # Low-level AT helpers
    # ------------------------------------------------------------------

    def _at(self, command: str, expect_ok: bool = True, wait: float | None = None) -> str:
        if self.mock:
            raise Sim800CError("AT commands are not used in mock mode")
        if not self._ser or not self._ser.is_open:
            raise Sim800CError("Modem is not connected")

        deadline = wait if wait is not None else self.timeout
        self._ser.write((command + "\r").encode("utf-8", errors="ignore"))
        self._ser.flush()

        response = self._read_until(("OK", "ERROR", "+CMS ERROR", "+CME ERROR"), deadline)
        if expect_ok and "OK" not in response:
            raise Sim800CError(f"Command failed: {command!r} -> {response!r}")
        return response

    def _read_until(self, markers: tuple[str, ...], timeout: float) -> str:
        assert self._ser is not None
        end = time.time() + timeout
        chunks: list[str] = []
        while time.time() < end:
            waiting = self._ser.in_waiting
            if waiting:
                data = self._ser.read(waiting).decode("utf-8", errors="ignore")
                chunks.append(data)
                joined = "".join(chunks)
                if any(m in joined for m in markers):
                    return joined
            else:
                time.sleep(0.05)
        return "".join(chunks)

    @staticmethod
    def _needs_ucs2(text: str) -> bool:
        return any(ch not in _GSM_CHARS for ch in text)

    @staticmethod
    def _to_ucs2_hex(text: str) -> str:
        return text.encode("utf-16-be").hex().upper()

    @staticmethod
    def _decode_sms_body(body: str) -> str:
        compact = "".join(body.split())
        if len(compact) >= 4 and len(compact) % 2 == 0 and re.fullmatch(r"[0-9A-Fa-f]+", compact):
            try:
                decoded = bytes.fromhex(compact).decode("utf-16-be")
                if decoded and any(ch.isprintable() or ch.isspace() for ch in decoded):
                    return decoded
            except (ValueError, UnicodeDecodeError):
                pass
        return body

    def _send_sms_pdu_text(self, number: str, message: str, unicode: bool = False) -> None:
        """Send SMS in text mode (CMGS)."""
        if not self._ser or not self._ser.is_open:
            raise Sim800CError("Modem is not connected")

        safe = message.replace("\x1a", "").replace("\x1b", "")
        dest = number
        payload = safe
        if unicode:
            self._at('AT+CSCS="UCS2"')
            self._at("AT+CSMP=17,167,0,8")
            dest = self._to_ucs2_hex(number)
            payload = self._to_ucs2_hex(safe)
        else:
            self._at('AT+CSCS="GSM"')
            try:
                self._at("AT+CSMP=17,167,0,0")
            except Sim800CError:
                pass

        self._ser.write(f'AT+CMGS="{dest}"\r'.encode("utf-8", errors="ignore"))
        self._ser.flush()

        # Wait for '>' prompt
        prompt = self._read_until((">", "ERROR"), self.timeout)
        if ">" not in prompt:
            raise Sim800CError(f"Modem did not accept CMGS: {prompt!r}")

        self._ser.write(payload.encode("utf-8", errors="ignore"))
        self._ser.write(b"\x1a")  # Ctrl+Z
        self._ser.flush()

        response = self._read_until(("OK", "ERROR", "+CMS ERROR"), max(self.timeout, 30.0))
        if unicode:
            try:
                self._at('AT+CSCS="GSM"')
                self._at("AT+CSMP=17,167,0,0")
            except Sim800CError:
                pass
        if "OK" not in response:
            raise Sim800CError(f"SMS send failed: {response!r}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_status(self) -> ModemStatus:
        with self._lock:
            if self.mock:
                return self.status
            if not self._ser or not self._ser.is_open:
                self.status.connected = False
                return self.status

            try:
                csq = self._at("AT+CSQ")
                m = re.search(r"\+CSQ:\s*(\d+),(\d+)", csq)
                if m:
                    self.status.signal_rssi = int(m.group(1))
                    self.status.signal_ber = int(m.group(2))

                creg = self._at("AT+CREG?")
                m = re.search(r"\+CREG:\s*\d+,(\d+)", creg)
                if m:
                    self.status.network_registered = m.group(1) in ("1", "5")

                try:
                    cops = self._at("AT+COPS?")
                    m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', cops)
                    if m:
                        self.status.operator = m.group(1)
                except Sim800CError:
                    pass

                try:
                    imei = self._at("AT+GSN")
                    lines = [
                        ln.strip()
                        for ln in imei.splitlines()
                        if ln.strip() and ln.strip() not in ("OK", "AT+GSN")
                    ]
                    if lines:
                        self.status.imei = lines[0]
                except Sim800CError:
                    pass

                try:
                    cpin = self._at("AT+CPIN?")
                    self.status.sim_ready = "READY" in cpin
                except Sim800CError:
                    self.status.sim_ready = False

                self.status.last_error = ""
            except Sim800CError as exc:
                self.status.last_error = str(exc)
            return self.status

    def send_sms(self, number: str, message: str) -> None:
        number = number.strip()
        message = message.strip()
        if not number:
            raise Sim800CError("Phone number is required")
        if not message:
            raise Sim800CError("Message body is required")
        if len(message) > 1600:
            raise Sim800CError("Message too long (max 1600 characters)")

        with self._lock:
            if self.mock:
                if not self.status.connected:
                    raise Sim800CError("Modem is not connected")
                time.sleep(0.15)  # simulate radio delay
                logger.info("Mock SMS sent to %s: %s", number, message[:80])
                return

            if not self.status.connected:
                raise Sim800CError("Modem is not connected")
            self._send_sms_pdu_text(number, message, unicode=self._needs_ucs2(message))

    def list_sms(self, status_filter: str = "ALL") -> list[RawSms]:
        with self._lock:
            if self.mock:
                return list(self._mock_inbox)

            raw = self._at(f'AT+CMGL="{status_filter}"', wait=max(self.timeout, 15.0))
            return self._parse_cmgl(raw)

    def delete_sms(self, index: int) -> None:
        with self._lock:
            if self.mock:
                self._mock_inbox = [m for m in self._mock_inbox if m.index != index]
                return
            self._at(f"AT+CMGD={index}")

    def delete_all_sms(self) -> None:
        with self._lock:
            if self.mock:
                self._mock_inbox.clear()
                return
            # Prefer delete-all flag; fall back to iterating
            try:
                self._at("AT+CMGD=1,4")
            except Sim800CError:
                for msg in self.list_sms("ALL"):
                    try:
                        self._at(f"AT+CMGD={msg.index}")
                    except Sim800CError:
                        pass

    def inject_mock_sms(self, sender: str, body: str) -> RawSms:
        """Test helper: push an inbound SMS into the mock inbox."""
        with self._lock:
            if not self.mock:
                raise Sim800CError("inject_mock_sms only works in mock mode")
            msg = RawSms(
                index=self._mock_next_index,
                status="REC UNREAD",
                sender=sender,
                timestamp=datetime.now(timezone.utc),
                body=body,
            )
            self._mock_next_index += 1
            self._mock_inbox.insert(0, msg)
            return msg

    @staticmethod
    def _parse_cmgl(raw: str) -> list[RawSms]:
        """Parse AT+CMGL response into RawSms list.

        Typical line:
        +CMGL: 1,"REC UNREAD","+15551234567",,"26/03/15,10:54:49+32"
        """
        pattern = re.compile(
            r'\+CMGL:\s*(\d+),"([^"]*)","([^"]*)",[^,\r\n]*,(?:"([^"]*)")?',
            re.MULTILINE,
        )
        matches = list(pattern.finditer(raw))
        messages: list[RawSms] = []
        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[start:end]
            body = re.sub(r"\r?\nOK\s*$", "", body).strip("\r\n").strip()
            body = Sim800CModem._decode_sms_body(body)

            ts = None
            if match.group(4):
                ts = Sim800CModem._parse_sms_timestamp(match.group(4))

            messages.append(
                RawSms(
                    index=int(match.group(1)),
                    status=match.group(2),
                    sender=match.group(3),
                    timestamp=ts,
                    body=body,
                )
            )
        return messages

    @staticmethod
    def _parse_sms_timestamp(value: str) -> Optional[datetime]:
        # Typical: 26/03/15,10:54:49+32  (yy/mm/dd,hh:mm:ss±tz quarters)
        try:
            cleaned = value.strip().split("+")[0].split("-")[0]
            return datetime.strptime(cleaned, "%y/%m/%d,%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


# Module-level singleton managed by the app lifespan
modem: Optional[Sim800CModem] = None


def get_modem() -> Sim800CModem:
    if modem is None:
        raise Sim800CError("Modem has not been initialized")
    return modem
