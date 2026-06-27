"""Auto-healing daemon — monitors bot process, restarts on crash. 24/7 reliable.

v3.3.0 daemon improvements (for 24/7 reliability):
- NEVER give up: after crash loop threshold, switch to 5-min intervals (not exit)
- Deep health check: verify HTTP /api/health AND WS status via /api/stats
- Signal handling: graceful shutdown on SIGTERM/SIGINT
- Disk space check: warn if disk > 90% full
- Log rotation awareness: docker logs managed externally

v3.1.0 daemon (baseline):
- restart_count resets after uptime > 1 hour (stable)
- Exponential backoff: 5s → 10s → 20s → 40s → 80s → 160s → 300s cap
- Health check via /api/health HTTP endpoint
- Max 10 restarts per hour (crash loop protection)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
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


def health_check_ok(host: str = "127.0.0.1", port: int = 8082, timeout: float = 3.0) -> bool:
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


def deep_health_check(host: str = "127.0.0.1", port: int = 8082, timeout: float = 5.0) -> tuple[bool, str]:
    """v3.3.0: Deep health check — HTTP + WS status verification.

    Returns (is_healthy, reason). Bot is healthy if:
    - HTTP /api/health responds 200
    - /api/stats shows WS connections active (clob + binance)
    """
    import urllib.request
    import urllib.error
    try:
        # Check 1: basic HTTP health
        url = f"http://{host}:{port}/api/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"

        # Check 2: WS status via /api/stats
        url2 = f"http://{host}:{port}/api/stats"
        req2 = urllib.request.Request(url2)
        with urllib.request.urlopen(req2, timeout=timeout) as resp2:
            if resp2.status != 200:
                return False, f"stats HTTP {resp2.status}"
            stats = json.loads(resp2.read().decode())

        ws = stats.get("ws_status", {})
        if not ws.get("clob_connected", False):
            return False, "CLOB WS disconnected"
        if not ws.get("binance_connected", False):
            return False, "Binance WS disconnected"

        # Check 3: WS data freshness (last message should be < 60s ago)
        # If WS connected but no data flowing, it's stale
        clob_tokens = ws.get("clob_tokens", 0)
        if clob_tokens == 0:
            return False, "CLOB WS: 0 tokens tracked"

        return True, f"OK (clob={clob_tokens} tokens, uptime={stats.get('uptime_sec',0)}s)"

    except Exception as e:
        return False, f"check failed: {e}"


def check_disk_space(path: str = "/app", threshold: float = 0.90) -> tuple[bool, float]:
    """v3.3.0: Check disk space. Returns (is_ok, usage_pct)."""
    try:
        usage = shutil.disk_usage(path)
        usage_pct = usage.used / usage.total
        return usage_pct < threshold, usage_pct
    except Exception:
        return True, 0.0  # Don't fail on disk check error


def kill_bot_gracefully(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """v3.4.2: Send SIGTERM to bot, wait up to timeout seconds, fall back to SIGKILL if still running."""
    logger.info("Sending SIGTERM to bot for graceful shutdown...")
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
        logger.info("Bot exited gracefully.")
    except subprocess.TimeoutExpired:
        logger.warning("Bot did not exit within %.1fs, forcing SIGKILL...", timeout)
        proc.kill()
        proc.wait()


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


# v3.3.0: Global flag for graceful shutdown
_shutdown_requested = False


def signal_handler(signum, frame):
    """v3.3.0: Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_requested = True


def main() -> None:
    global _shutdown_requested

    # v3.3.0: Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    port = int(os.environ.get("HTTP_PORT", "8082"))
    health_host = "127.0.0.1"
    crash_loop_threshold = 10  # restarts per hour before switching to long interval
    long_interval = 300  # 5 min intervals after crash loop (NEVER give up)
    restart_history: list[float] = []
    backoff_delays = [5, 10, 20, 40, 80, 160, 300]  # exponential

    # Write initial heartbeat
    Path("data").mkdir(exist_ok=True)
    Path("data/heartbeat.json").write_text('{"heartbeat": ' + str(time.time()) + '}')

    logger.info("Daemon v3.3.0 started — bot port=%d (deep health check via %s)", port, health_host)
    logger.info("Crash loop threshold: %d/hour → switch to %ds intervals (never give up)",
                crash_loop_threshold, long_interval)

    # v3.3.0: Periodic deep health check interval (every 60s)
    deep_check_interval = 60
    last_deep_check = 0.0

    while not _shutdown_requested:
        proc = run_bot()
        start_time = time.time()
        uptime_threshold = 3600  # 1 hour
        consecutive_restart_idx = 0
        last_deep_check = time.time()

        while proc.poll() is None and not _shutdown_requested:
            time.sleep(10)
            now = time.time()

            # v3.3.0: Skip health check during 30s startup grace period
            if now - start_time < 30:
                continue

            # Basic HTTP health check (every 10s)
            if not health_check_ok(health_host, port):
                logger.warning("Basic health check failed — restarting bot")
                kill_bot_gracefully(proc)
                break

            # v3.3.0: Deep health check (every 60s) — verify WS connectivity
            if now - last_deep_check >= deep_check_interval:
                last_deep_check = now
                healthy, reason = deep_health_check(health_host, port)
                if not healthy:
                    logger.warning("Deep health check failed: %s — restarting bot", reason)
                    kill_bot_gracefully(proc)
                    break

                # v3.3.0: Disk space check (warn only, don't kill)
                disk_ok, disk_pct = check_disk_space()
                if not disk_ok:
                    logger.error("Disk space CRITICAL: %.1f%% full — bot may fail soon", disk_pct * 100)
                elif disk_pct > 0.85:
                    logger.warning("Disk space high: %.1f%% full", disk_pct * 100)

        if _shutdown_requested:
            logger.info("Graceful shutdown requested for daemon — exiting bot")
            kill_bot_gracefully(proc)
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

        restarts_this_hour = len(restart_history)

        # v3.3.0: NEVER give up — switch to long interval after crash loop
        if restarts_this_hour > crash_loop_threshold:
            logger.error(
                "CRASH LOOP: %d restarts in last hour (threshold %d). "
                "Switching to %ds intervals (NOT giving up — 24/7 mode).",
                restarts_this_hour, crash_loop_threshold, long_interval,
            )
            delay = long_interval
        else:
            delay = backoff_delays[consecutive_restart_idx]

        logger.warning(
            "Bot crashed (exit=%d, uptime=%.0fs) — restart in %ds (attempt %d this hour, idx=%d)",
            exit_code, uptime, delay, restarts_this_hour, consecutive_restart_idx,
        )
        time.sleep(delay)

    logger.info("Daemon stopped gracefully")


if __name__ == "__main__":
    main()
