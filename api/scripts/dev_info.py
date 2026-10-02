"""Print how to reach the local dev server from the iPhone, plus the resolved config.

    python -m scripts.dev_info [--port 8080]       # from api/; `make dev` runs this first

Prints
  - the Mac's LAN IPv4 address(es): the phone must be on the same Wi-Fi and use one of them
    (http://<ip>:8080). iOS needs an ATS exception for plain http on a LAN IP in debug builds.
  - local URLs: /health, /health?deep=1, /docs, /mock/health, /mock/fixtures
  - an env summary from core.config (store, auth, ask engine, models, features, Gemini
    credentials hint). Secrets are never printed.

Debugging "the phone can't connect": check the IP printed here matches System Settings >
Wi-Fi > Details, macOS firewall allows incoming connections for Python, and uvicorn is
bound to 0.0.0.0 (make dev does that), not 127.0.0.1.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


def _primary_ip() -> str | None:
    """IP of the interface that routes to the internet. UDP connect sends no packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None
    finally:
        s.close()


def _mac_ifaddrs() -> list[str]:
    """macOS: `ipconfig getifaddr en0..en3` (Wi-Fi is usually en0)."""
    out: list[str] = []
    if sys.platform != "darwin":
        return out
    for iface in ("en0", "en1", "en2", "en3"):
        try:
            r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=1)
        except (OSError, subprocess.SubprocessError):
            continue
        ip = r.stdout.strip()
        if r.returncode == 0 and ip:
            out.append(ip)
    return out


def lan_ips() -> list[str]:
    """Distinct non-loopback IPv4 addresses, most likely first."""
    ips: list[str] = []
    for ip in [_primary_ip(), *_mac_ifaddrs()]:
        if ip and ip not in ips and not ip.startswith("127."):
            ips.append(ip)
    if not ips:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and not ip.startswith("127."):
                    ips.append(ip)
        except OSError:
            pass
    return ips


def urls(host: str, port: int) -> list[str]:
    base = f"http://{host}:{port}"
    return [f"{base}/health", f"{base}/health?deep=1", f"{base}/docs", f"{base}/mock/health",
            f"{base}/mock/fixtures"]


def env_summary() -> list[str]:
    """Key settings, no secrets. Imports core.config lazily so a broken env still prints IPs."""
    try:
        from core.config import get_settings

        s = get_settings()
    except Exception as e:  # noqa: BLE001
        return [f"config failed to load: {e!r}"]
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    creds = ("GOOGLE_APPLICATION_CREDENTIALS set" if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
             else "ADC file present" if adc.exists()
             else "NO ADC: run `gcloud auth application-default login` (Gemini + Firestore off)")
    off = [k for k, v in s.features.items() if not v]
    return [
        f"project={s.project}  gemini_location={s.gemini_location}  credentials: {creds}",
        f"store={s.store}  auth={s.auth_mode}  ask_engine={s.ask_engine}  log={s.log_format}/{s.log_level}",
        f"models: label={s.model_label} question={s.model_question} text={s.model_text} "
        f"price={s.model_price} embed={s.model_embedding}",
        f"features off: {', '.join(off) or 'none'}  usda_key={'set' if s.usda_api_key else 'unset'}",
        f"fixtures_dir={s.fixtures_dir}{'' if Path(s.fixtures_dir).exists() else '  (MISSING)'}",
    ]


def render(port: int, ips: list[str] | None = None) -> str:
    ips = lan_ips() if ips is None else ips
    lines = ["", "Pear API dev server", "==================="]
    if ips:
        lines.append(f"iPhone (same Wi-Fi): set the app's base URL to http://{ips[0]}:{port}")
        lines += [f"  {u}" for u in urls(ips[0], port)]
        lines += [f"  {urls(ip, port)[0]}   (other interface)" for ip in ips[1:]]
    else:
        lines.append("No LAN IPv4 found (offline?). The phone can't reach this Mac right now.")
    lines.append("This Mac:")
    lines += [f"  {u}" for u in urls("localhost", port)]
    lines.append("Env:")
    lines += [f"  {x}" for x in env_summary()]
    lines.append(f"Smoke test: make smoke URL=http://localhost:{port}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.dev_info")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = p.parse_args(argv)
    print(render(args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
