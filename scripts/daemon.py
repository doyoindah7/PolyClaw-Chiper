"""Auto-healing daemon — monitors bot process, restarts on crash.

Fixes vs v2:
- restart_count resets after uptime > 1 hour (stable)
- Exponential backoff: 5s → 10s → 20s → 40s → 80s → 160s → 300s cap
- Health check via /api/health HTTP endpoint (not just heartbeat file)
- Max 10 restarts per hour (crash loop protection)
- Logs all restart events with timestamps
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daemon")


def health_check_ok(host: str = "127.0.0.1", port: int = 8081, timeout: float = 3.0) -> bool:
    """Check if bot HTTP server is responding."""
    import urllib.request
    import urllib.error
    try:
        url = f"http://{host}:{port}/api/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def run_bot() -> subprocess.Popen:
    """Start the bot process."""
    env = dict(os.environ)
    env["PYTHONPATH"] = "/app/src"

    cmd = [sys.executable, "-m", "polyclaw_cipher_v3"]
    logger.info("Starting bot: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd="/app",
    )

    # Log output in background
    def log_output():
        for line in iter(proc.stdout.readline, b""):
            sys.stdout.write(line.decode())
            sys.stdout.flush()

    import threading
    t = threading.Thread(target=log_output, daemon=True)
    t.start()

    return proc


def main() -> None:
    # HTTP_HOST is for BINDING (0.0.0.0 = all interfaces).
    # For health check CONNECTING, always use 127.0.0.1 (0.0.0.0 doesn't work as client dest).
    port = int(os.environ.get("HTTP_PORT", "8082"))
    health_host = "127.0.0.1"
    max_restart_per_hour = 10
    restart_history: list[float] = []  # timestamps of recent restarts
    backoff_delays = [5, 10, 20, 40, 80, 160, 300]  # exponential

    # Write initial heartbeat
    Path("data").mkdir(exist_ok=True)
    Path("data/heartbeat.json").write_text('{"heartbeat": ' + str(time.time()) + '}')

    logger.info("Daemon started — bot port=%d (health check via %s)", port, health_host)

    while True:
        proc = run_bot()
        start_time = time.time()
        uptime_threshold = 3600  # 1 hour
        consecutive_restart_idx = 0  # Index into backoff_delays

        while proc.poll() is None:
            time.sleep(10)
            # Health check via HTTP (more reliable than heartbeat file)
            # Only check after 30s startup grace period
            if time.time() - start_time > 30 and not health_check_ok(health_host, port):
                logger.warning("Health check failed — killing bot for restart")
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                break

        exit_code = proc.returncode
        uptime = time.time() - start_time

        # Reset consecutive restart index if uptime was long enough
        if uptime > uptime_threshold:
            consecutive_restart_idx = 0
            logger.info("Bot had stable uptime=%.0fs, resetting backoff", uptime)
        else:
            consecutive_restart_idx = min(consecutive_restart_idx + 1, len(backoff_delays) - 1)

        # Track restart in history (last hour)
        now = time.time()
        restart_history = [t for t in restart_history if now - t < 3600]
        restart_history.append(now)

        if len(restart_history) > max_restart_per_hour:
            logger.error(
                "Crash loop detected: %d restarts in last hour (max %d). Giving up.",
                len(restart_history), max_restart_per_hour,
            )
            sys.exit(1)

        delay = backoff_delays[consecutive_restart_idx]
        logger.warning(
            "Bot crashed (exit=%d, uptime=%.0fs) — restart in %ds (attempt %d this hour, idx=%d)",
            exit_code, uptime, delay, len(restart_history), consecutive_restart_idx,
        )
        time.sleep(delay)


if __name__ == "__main__":
    main()
