"""Auto-healing daemon — monitors bot process, restarts on crash.

The daemon:
1. Spawns the bot as a subprocess
2. Monitors heartbeat file (data/wallet.json heartbeat field)
3. If heartbeat goes stale >30s → kill & restart
4. If process exits → restart after 5s delay
5. Logs all restart events
"""
from __future__ import annotations
import json
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


def get_heartbeat_age(heartbeat_path: str = "data/wallet.json") -> float:
    """Return seconds since last heartbeat update."""
    try:
        data = json.loads(Path(heartbeat_path).read_text())
        hb = data.get("heartbeat", 0)
        return time.time() - hb if hb else 999.0
    except Exception:
        return 999.0


def run_bot():
    """Start the bot process."""
    env = dict(os.environ)
    env["PYTHONPATH"] = "/app/src"

    cmd = [sys.executable, "-m", "polyclaw_cipher"]
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


def main():
    max_heartbeat_age = 30  # seconds
    restart_delay = 5
    max_restarts = 50
    restart_count = 0

    # Write heartbeat file initially
    Path("data").mkdir(exist_ok=True)
    Path("data/heartbeat.json").write_text(json.dumps({"heartbeat": time.time()}))

    while restart_count < max_restarts:
        proc = run_bot()
        start_time = time.time()

        while proc.poll() is None:
            time.sleep(5)

            # Check heartbeat
            hb_age = get_heartbeat_age("data/wallet.json")
            if hb_age > max_heartbeat_age:
                logger.warning(
                    "Heartbeat stale (%.0fs) — killing bot for restart", hb_age
                )
                proc.kill()
                proc.wait(timeout=10)
                break

        exit_code = proc.returncode
        uptime = time.time() - start_time
        restart_count += 1

        if exit_code == 0:
            logger.info("Bot exited cleanly (uptime=%.0fs)", uptime)
            break

        logger.warning(
            "Bot crashed (exit=%d, uptime=%.0fs) — restart %d/%d in %ds",
            exit_code, uptime, restart_count, max_restarts, restart_delay,
        )
        time.sleep(restart_delay)

    if restart_count >= max_restarts:
        logger.error("Max restarts (%d) reached — giving up", max_restarts)
        sys.exit(1)


if __name__ == "__main__":
    main()
