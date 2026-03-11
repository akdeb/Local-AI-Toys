import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def list_serial_ports() -> List[str]:
    try:
        from serial.tools import list_ports  # type: ignore

        ports = [p.device for p in list_ports.comports() if getattr(p, "device", None)]
        ports = [p for p in ports if isinstance(p, str) and p]
        return sorted(list(dict.fromkeys(ports)))
    except Exception:
        paths = []
        paths.extend(Path("/dev").glob("tty.*"))
        paths.extend(Path("/dev").glob("cu.*"))
        return sorted(list(dict.fromkeys([str(p) for p in paths])))


def _has_flash_images(base_dir: Path) -> bool:
    return (
        (base_dir / "bootloader.bin").exists()
        and (base_dir / "partitions.bin").exists()
        and (base_dir / "firmware.bin").exists()
    )


def _repo_root() -> Path:
    # Deterministic for local-dev layout:
    # resources/python-backend/services/firmware.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def resolve_firmware_dir() -> Optional[Path]:
    env_dir = (os.environ.get("ELATO_FIRMWARE_DIR") or "").strip()
    base_dir = Path(env_dir).expanduser().resolve() if env_dir else _repo_root() / "resources" / "firmware"
    if _has_flash_images(base_dir):
        return base_dir
    return None


def _find_arduino_dir() -> Optional[Path]:
    # Explicit override wins; otherwise deterministic repo-root path.
    env_dir = (os.environ.get("ELATO_ARDUINO_DIR") or "").strip()
    if env_dir:
        d = Path(env_dir).expanduser().resolve()
        if (d / "platformio.ini").exists():
            return d

    repo_arduino = _repo_root() / "arduino"
    if (repo_arduino / "platformio.ini").exists():
        return repo_arduino
    return None


def _extract_default_env(platformio_ini: Path) -> str:
    try:
        text = platformio_ini.read_text(encoding="utf-8")
    except Exception:
        return "esp32-s3-devkitc-1"
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[env:") and s.endswith("]"):
            return s[5:-1]
    return "esp32-s3-devkitc-1"


def _build_firmware_with_platformio(arduino_dir: Path) -> Tuple[Optional[Path], str]:
    env_name = _extract_default_env(arduino_dir / "platformio.ini")
    cmd = ["platformio", "run", "-e", env_name]
    proc = subprocess.run(
        cmd,
        cwd=str(arduino_dir),
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
    if proc.returncode != 0:
        return None, output

    out_dir = arduino_dir / ".pio" / "build" / env_name
    if _has_flash_images(out_dir):
        return out_dir, output
    return None, output + "\nBuild succeeded but expected flash images were not found."


def prepare_firmware_images(auto_build: bool = True) -> Tuple[Optional[Path], str]:
    existing = resolve_firmware_dir()
    if existing:
        return existing, f"Using firmware images from: {existing}"

    if not auto_build:
        return None, "Firmware images are missing and auto-build is disabled."

    arduino_dir = _find_arduino_dir()
    if not arduino_dir:
        return None, "Firmware images are missing and no Arduino project was found to build."

    built_dir, build_log = _build_firmware_with_platformio(arduino_dir)
    if not built_dir:
        return None, "Firmware build failed.\n" + build_log

    return built_dir, f"Built firmware using PlatformIO in {arduino_dir}\n{build_log}"


def firmware_bin_path() -> Path:
    resolved = resolve_firmware_dir()
    if resolved:
        return resolved / "firmware.bin"
    env_dir = (os.environ.get("ELATO_FIRMWARE_DIR") or "").strip()
    base_dir = Path(env_dir).expanduser().resolve() if env_dir else _repo_root() / "resources" / "firmware"
    return base_dir / "firmware.bin"


def _resolve_flash_files(firmware_path: Path, offset: str) -> List[Tuple[str, Path]]:
    base_dir = firmware_path.parent
    bootloader = base_dir / "bootloader.bin"
    partitions = base_dir / "partitions.bin"
    firmware = base_dir / "firmware.bin"

    if bootloader.exists() and partitions.exists() and firmware.exists():
        return [
            ("0x0000", bootloader),
            ("0x8000", partitions),
            ("0x10000", firmware),
        ]

    # Fallback for builds that only ship app firmware.
    return [(offset, firmware_path)]


def run_firmware_flash(
    *,
    port: str,
    baud: int,
    chip: str,
    offset: str,
    firmware_path: Path,
) -> Dict[str, object]:
    flash_files = _resolve_flash_files(firmware_path, offset)
    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "--before",
        "default-reset",
        "--after",
        "hard-reset",
        "--chip",
        chip,
        "--port",
        port,
        "--baud",
        str(baud),
        "write-flash",
        "-z",
    ]
    for flash_offset, flash_path in flash_files:
        cmd.append(flash_offset)
        cmd.append(str(flash_path))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": " ".join(cmd),
        "output": out,
    }
