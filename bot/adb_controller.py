from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


class ADBError(Exception):
    pass


class DeviceNotFoundError(ADBError):
    pass


class ADBTimeoutError(ADBError):
    pass


ADBCommandError = ADBError
ADBConnectionError = DeviceNotFoundError


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    width: int
    height: int


class ADBController:
    def __init__(self, adb_path: str | object, serial: str | None = None, exec_timeout_s: float = 15.0, logger: object | None = None) -> None:
        if hasattr(adb_path, "raw"):
            section = adb_path.raw.get("adb", {})
            adb_path = section.get("adb_path") or "adb"
            serial = section.get("device_serial") or None
            if isinstance(serial, str) and serial.strip().lower() in ("", "auto"):
                serial = None
            exec_timeout_s = float(section.get("command_timeout_s", 15.0))
        if exec_timeout_s <= 0:
            raise ValueError("exec_timeout_s must be > 0")
        self._adb_path = str(adb_path)
        self._serial = serial.strip() if isinstance(serial, str) and serial.strip() else None
        self._exec_timeout_s = float(exec_timeout_s)
        self._device_info: DeviceInfo | None = None
        self._logger = logger

    def connect(self) -> DeviceInfo:
        serial = self._serial or self._detect_device_serial()
        width, height = self.get_screen_size(serial)
        info = DeviceInfo(serial=serial, width=width, height=height)
        self._serial = serial
        self._device_info = info
        return info

    def device_info(self) -> DeviceInfo:
        if self._device_info is None:
            raise DeviceNotFoundError("device is not connected; call connect() first")
        return self._device_info

    def shell(self, command: str) -> str:
        self.check_connection()
        completed = self._run_adb(["shell", command], text=True)
        return completed.stdout

    def tap(self, x: int, y: int) -> None:
        if x < 0 or y < 0:
            raise ValueError("tap coordinates must be non-negative")
        self.shell(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        if duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def back(self) -> None:
        self.keyevent(4)

    def keyevent(self, keycode: int) -> None:
        self.shell(f"input keyevent {keycode}")

    def screencap_png(self) -> bytes:
        self.check_connection()
        completed = self._run_adb(["exec-out", "screencap", "-p"], text=False)
        data = completed.stdout
        if not isinstance(data, bytes) or not data:
            raise ADBError("empty screencap output")
        return data

    def check_connection(self) -> bool:
        serial = self._serial or self._detect_device_serial()
        completed = self._run_adb(["get-state"], serial=serial, text=True)
        state = completed.stdout.strip().lower()
        if state != "device":
            raise DeviceNotFoundError(f"device '{serial}' is not ready: {state!r}")
        if self._serial is None:
            self._serial = serial
        return True

    def get_screen_size(self, serial: str | None = None) -> tuple[int, int]:
        target_serial = serial or self._serial or self._detect_device_serial()
        completed = self._run_adb(["shell", "wm size"], serial=target_serial, text=True)
        output = completed.stdout.strip()
        match = re.search(r"Physical size:\s*(\d+)x(\d+)", output) or re.search(r"Override size:\s*(\d+)x(\d+)", output) or re.search(r"(\d+)x(\d+)", output)
        if match is None:
            raise ADBError(f"unable to parse screen size from output: {output!r}")
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
            raise ADBError(f"invalid screen size parsed: {width}x{height}")
        return width, height

    screen_size = get_screen_size

    def _detect_device_serial(self) -> str:
        completed = self._run_adb(["devices"], text=True)
        serials = [parts[0] for line in completed.stdout.splitlines()[1:] if len(parts := line.strip().split()) >= 2 and parts[1] == "device"]
        if not serials:
            raise DeviceNotFoundError("no adb devices found")
        if len(serials) > 1:
            raise DeviceNotFoundError("multiple adb devices found; specify serial explicitly")
        return serials[0]

    def _run_adb(self, args: list[str], serial: str | None = None, text: bool = True) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        cmd = [self._adb_path]
        target_serial = serial or self._serial
        if target_serial:
            cmd.extend(["-s", target_serial])
        cmd.extend(args)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=text, timeout=self._exec_timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ADBTimeoutError(f"adb command timed out: {' '.join(cmd)}") from exc
        except FileNotFoundError as exc:
            raise ADBError(f"adb executable not found: {self._adb_path!r}") from exc
        except OSError as exc:
            raise ADBError(f"failed to execute adb command: {' '.join(cmd)}") from exc
        self._raise_for_completed(completed, f"{self._adb_path} {' '.join(args)}")
        return completed

    def _raise_for_completed(self, completed: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes], cmd_hint: str) -> None:
        if completed.returncode == 0:
            return
        stdout = completed.stdout.decode(errors="replace") if isinstance(completed.stdout, bytes) else completed.stdout
        stderr = completed.stderr.decode(errors="replace") if isinstance(completed.stderr, bytes) else completed.stderr
        details = " | ".join(part for part in [stdout.strip(), stderr.strip()] if part).lower()
        if any(marker in details for marker in ("device not found", "no devices/emulators found", "more than one device/emulator", "offline", "unauthorized")):
            raise DeviceNotFoundError(f"{cmd_hint} failed: {details or 'device unavailable'}")
        raise ADBError(f"{cmd_hint} failed with code {completed.returncode}: {details or 'unknown adb error'}")
