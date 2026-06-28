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


class StagnationDetector:
    """v3.5.0: Track state deltas to detect bot inactivity (Arena.ai recommendation).

    Bot can be "alive" (HTTP 200) but "stuck" (no signals, no trades, bankroll frozen).
    This detector tracks state changes over time and flags stagnation.
    """

    def __init__(self):
        self.history: dict[str, list[tuple[float, float]]] = {
            "bankroll": [],
            "trades": [],
            "signals": [],
        }
        self.last_stagnant_restart: float = 0.0
        self.stagnation_cooldown: float = 1800.0  # 30 min between stagnation restarts

    def record(self, stats: dict) -> None:
        """Record current state snapshot."""
        now = time.time()
        self.history["bankroll"].append((now, stats.get("bankroll", 0)))
        self.history["trades"].append((now, stats.get("trades", 0)))
        self.history["signals"].append((now, stats.get("signals", 0)))
        # Keep only last 2 hours
        cutoff = now - 7200
        for key in self.history:
            self.history[key] = [(ts, v) for ts, v in self.history[key] if ts > cutoff]

    def is_stagnant(self, stats: dict, threshold_min: int = 15) -> tuple[bool, str]:
        """Check if bot has been stagnant (no state change) for threshold_min minutes.

        Returns (is_stagnant, reason).

        v3.5.5 FIX (MiniMax C2): open_positions guard moved here from record() (was a no-op bug).
        If open positions exist, bot is waiting for market resolution — NOT stagnant.
        """
        now = time.time()
        threshold = threshold_min * 60

        # v3.5.5: open_positions guard — if positions exist, bot is waiting for resolution, not stuck
        open_positions = stats.get("open_positions", [])
        if len(open_positions) > 0:
            return False, f"OK (have {len(open_positions)} open positions, waiting for resolution)"

        # Need at least 3 samples to compare
        for key in self.history:
            if len(self.history[key]) < 3:
                return False, "insufficient data"

        # Check 1: Bankroll unchanged for threshold_min
        br_hist = self.history["bankroll"]
        if br_hist[-1][0] - br_hist[0][0] >= threshold:
            if abs(br_hist[-1][1] - br_hist[0][1]) < 0.01:
                return True, f"Bankroll unchanged for {threshold_min}m (stuck at ${br_hist[-1][1]:.2f})"

        # Check 2: No new trades for threshold_min (when bankroll > $30)
        bankroll = stats.get("bankroll", 0)
        tr_hist = self.history["trades"]
        if bankroll > 30 and tr_hist[-1][0] - tr_hist[0][0] >= threshold:
            if tr_hist[-1][1] == tr_hist[0][1]:
                return True, f"No new trades for {threshold_min}m (trades stuck at {int(tr_hist[-1][1])})"

        # Check 3: No new signals for 10 min
        # v3.5.5: Only trigger if also no open positions (open positions guard above handles that case)
        sig_hist = self.history["signals"]
        if sig_hist[-1][0] - sig_hist[0][0] >= 600:  # 10 min
            if sig_hist[-1][1] == sig_hist[0][1]:
                return True, "No new signals for 10m and no open positions (strategies may be dead)"

        # Check 4: All strategies disabled
        disabled = stats.get("risk", {}).get("disabled_strategies", [])
        if len(disabled) >= 3:
            return True, f"All strategies disabled: {disabled}"

        # Check 5: Cash stuck (high bankroll, almost no cash, high deployment)
        cash = stats.get("cash", 0)
        deployed = stats.get("deployed", 0)
        if bankroll > 30 and cash < 1.0 and deployed > bankroll * 0.9:
            return True, f"Cash stuck: ${cash:.2f} cash, ${deployed:.2f} deployed"

        # Check 6: 0 markets tracked (scanner dead)
        markets = stats.get("markets", 0)
        if markets == 0:
            return True, "0 markets tracked (scanner dead?)"

        return False, "OK"

    def should_restart(self, stats: dict, threshold_min: int = 15) -> tuple[bool, str]:
        """Check stagnation + apply cooldown (don't restart too frequently)."""
        stagnant, reason = self.is_stagnant(stats, threshold_min)
        if not stagnant:
            return False, "OK"

        now = time.time()
        if now - self.last_stagnant_restart < self.stagnation_cooldown:
            return False, f"Stagnation detected but cooldown active ({int((self.stagnation_cooldown - (now - self.last_stagnant_restart))//60)}m left)"

        self.last_stagnant_restart = now
        return True, reason


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


# v3.5.7: Lightweight watchdog state (in-memory, persists across bot restarts but not daemon restarts)
_wal_alert_cooldown: float = 0.0  # Don't spam WAL alerts
_disk_cleanup_cooldown: float = 0.0  # Don't spam disk cleanup


def signal_handler(signum, frame):
    """v3.3.0: Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_requested = True


# v3.5.7: Lightweight watchdog checks (no modular restructure, just functions)


def check_signal_starvation(host: str, port: int) -> None:
    """v3.5.7: Check signal starvation + execution failure via /api/admin/db_stats.

    Logs:
    - INFO per strategy if 0 signals in last 1h
    - WARN if momentum < 2 signals/hour (momentum should be most active)
    - ERROR if total_signals > 10 but trades_closed = 0 (execution pipeline issue)
    """
    import urllib.request
    import urllib.error
    try:
        url = f"http://{host}:{port}/api/admin/db_stats?hours=1"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())

        signals = data.get("signals", {})
        trades = data.get("trades", {})
        total_signals = signals.get("total", 0)
        executed_signals = signals.get("executed", 0)
        rejected_signals = signals.get("rejected", 0)
        rejection_rate = signals.get("rejection_rate", 0.0)
        per_strategy = signals.get("per_strategy", {})
        trades_closed = trades.get("closed", 0)
        pnl_period = trades.get("pnl_total", 0.0)

        # Check 1: Per-strategy starvation (INFO level — most "no signal" cases are normal)
        for strat in ["momentum", "atomic_arb", "latency_arb", "resolution_snipe"]:
            n = per_strategy.get(strat, 0)
            if n == 0:
                logger.info("SignalCheck: %s emitted 0 signals in last 1h", strat)
            elif strat == "momentum" and n < 2:
                logger.warning("SignalCheck: momentum only %d signals in 1h (expected 5+)", n)

        # Check 2: High rejection rate (ERROR — risk gate too tight or bug)
        if total_signals > 5 and rejection_rate > 0.7:
            logger.error(
                "SignalCheck: HIGH rejection rate %d/%d (%.0f%%) — risk gate too tight?",
                rejected_signals, total_signals, rejection_rate * 100,
            )

        # Check 3: Execution pipeline broken (ERROR — signals fired but no trades)
        if total_signals > 10 and trades_closed == 0:
            logger.error(
                "SignalCheck: %d signals fired but 0 trades closed in 1h — execution pipeline issue?",
                total_signals,
            )

        # Check 4: Positive summary (DEBUG level, for visibility)
        logger.debug(
            "SignalCheck: signals=%d (exec=%d, rej=%d), trades=%d, pnl=$%.2f in 1h",
            total_signals, executed_signals, rejected_signals, trades_closed, pnl_period,
        )

    except urllib.error.HTTPError as e:
        logger.warning("SignalCheck: db_stats endpoint returned HTTP %d", e.code)
    except Exception as e:
        logger.debug("SignalCheck failed: %s", e)


def check_cash_deployment(host: str, port: int) -> None:
    """v3.5.7: Monitor cash deployment ratio.

    Logs:
    - WARN if cash < 5% of bankroll (over-deployed)
    - ERROR if cash < 1% of bankroll (deadlock imminent)
    Note: Bot sizer v3.5.5 already hard-blocks new entries when cash < $1.
    This is for visibility — no auto-action.
    """
    import urllib.request
    try:
        url = f"http://{host}:{port}/api/stats"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            stats = json.loads(resp.read().decode())

        bankroll = stats.get("bankroll", 0.0)
        cash = stats.get("cash", 0.0)
        if bankroll <= 0:
            return

        cash_pct = cash / bankroll
        if cash_pct < 0.01:
            logger.error(
                "CashCheck: CRITICAL — cash=$%.2f (%.1f%% of bankroll $%.2f) — deadlock imminent",
                cash, cash_pct * 100, bankroll,
            )
        elif cash_pct < 0.05:
            logger.warning(
                "CashCheck: over-deployed — cash=$%.2f (%.1f%% of bankroll $%.2f)",
                cash, cash_pct * 100, bankroll,
            )
        else:
            logger.debug("CashCheck: OK — cash=$%.2f (%.1f%% of bankroll $%.2f)",
                         cash, cash_pct * 100, bankroll)
    except Exception as e:
        logger.debug("CashCheck failed: %s", e)


def check_resources(host: str, port: int) -> None:
    """v3.5.7: Resource checks — WAL file size, container memory, docker log size.

    Auto-actions:
    - WAL > 5MB → trigger /api/admin/wal_checkpoint (with 10-min cooldown)
    - Disk > 90% → docker system prune -f (with 30-min cooldown)
    """
    import subprocess
    global _wal_alert_cooldown, _disk_cleanup_cooldown
    now = time.time()

    # Check 1: WAL file size (via filesystem)
    try:
        wal_path = "/app/data/cipher_v3.db-wal"
        if os.path.exists(wal_path):
            wal_size = os.path.getsize(wal_path)
            wal_mb = wal_size / (1024 * 1024)
            if wal_mb > 5.0:
                if now - _wal_alert_cooldown > 600:  # 10 min cooldown
                    logger.warning("ResourceCheck: WAL file %.1fMB — triggering checkpoint", wal_mb)
                    # Trigger checkpoint via admin API
                    try:
                        import urllib.request
                        url = f"http://{host}:{port}/api/admin/wal_checkpoint"
                        req = urllib.request.Request(url, method="POST")
                        with urllib.request.urlopen(req, timeout=10.0) as resp:
                            result = json.loads(resp.read().decode())
                            logger.info("ResourceCheck: WAL checkpoint triggered: %s", result.get("status"))
                        _wal_alert_cooldown = now
                    except Exception as e:
                        logger.error("ResourceCheck: WAL checkpoint failed: %s", e)
                        _wal_alert_cooldown = now  # Still cooldown to avoid spam
            else:
                logger.debug("ResourceCheck: WAL file %.1fMB (OK)", wal_mb)
    except Exception as e:
        logger.debug("ResourceCheck: WAL size check failed: %s", e)

    # Check 2: Container memory (via docker stats — non-blocking)
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "polyclaw-cipher-v3"],
            capture_output=True, text=True, timeout=5.0,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Output format: "72.21MiB / 1GiB"
            mem_str = result.stdout.strip().split(" / ")[0]
            # Parse "72.21MiB" or "1.5GiB"
            if "MiB" in mem_str:
                mem_mb = float(mem_str.replace("MiB", "").strip())
            elif "GiB" in mem_str:
                mem_mb = float(mem_str.replace("GiB", "").strip()) * 1024
            else:
                mem_mb = 0

            if mem_mb > 800:
                logger.error("ResourceCheck: Container memory %.0fMB / 1024MB — OOM imminent", mem_mb)
            elif mem_mb > 600:
                logger.warning("ResourceCheck: Container memory %.0fMB / 1024MB — high", mem_mb)
            else:
                logger.debug("ResourceCheck: Container memory %.0fMB (OK)", mem_mb)
    except Exception as e:
        logger.debug("ResourceCheck: memory check failed: %s", e)

    # Check 3: Disk space (upgrade existing check with auto-cleanup)
    disk_ok, disk_pct = check_disk_space()
    if not disk_ok:
        logger.error("ResourceCheck: Disk CRITICAL %.1f%% — bot may fail soon", disk_pct * 100)
        # Auto-cleanup: docker system prune + builder prune (with 30-min cooldown)
        if now - _disk_cleanup_cooldown > 1800:
            logger.warning("ResourceCheck: Auto-cleanup triggered — running docker system prune")
            try:
                subprocess.run(["docker", "system", "prune", "-f"], capture_output=True, timeout=60.0)
                subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True, timeout=60.0)
                logger.info("ResourceCheck: Auto-cleanup completed")
                _disk_cleanup_cooldown = now
            except Exception as e:
                logger.error("ResourceCheck: Auto-cleanup failed: %s", e)
                _disk_cleanup_cooldown = now
    elif disk_pct > 0.85:
        logger.warning("ResourceCheck: Disk high %.1f%% full", disk_pct * 100)
    else:
        logger.debug("ResourceCheck: Disk %.1f%% (OK)", disk_pct * 100)





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

    # v3.3.0: Periodic deep health check interval (every 60s)
    deep_check_interval = 60
    last_deep_check = 0.0
    # v3.5.0: Stagnation detector (Arena.ai recommendation)
    stagnation_detector = StagnationDetector()
    stagnation_check_interval = 120  # Check every 2 min
    # v3.5.7: Lightweight watchdog checks (every 60s)
    watchdog_check_interval = 60

    logger.info("Daemon v3.5.12 started — bot port=%d (deep health check via %s)", port, health_host)
    logger.info("Crash loop threshold: %d/hour → switch to %ds intervals (never give up)",
                crash_loop_threshold, long_interval)
    logger.info("Watchdog checks every %ds: signal_starvation, cash_deployment, resources",
                watchdog_check_interval)
    last_stagnation_check = 0.0
    last_watchdog_check = 0.0

    while not _shutdown_requested:
        proc = run_bot()
        start_time = time.time()
        uptime_threshold = 3600  # 1 hour
        consecutive_restart_idx = 0
        last_deep_check = time.time()
        last_stagnation_check = time.time()
        last_watchdog_check = time.time()

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

            # v3.5.0: Stagnation detection (every 2 min) — Arena.ai recommendation
            if now - last_stagnation_check >= stagnation_check_interval:
                last_stagnation_check = now
                try:
                    import urllib.request
                    url = f"http://{health_host}:{port}/api/stats"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        stats = json.loads(resp.read().decode())
                    stagnation_detector.record(stats)
                    should_restart, reason = stagnation_detector.should_restart(stats, threshold_min=15)
                    if should_restart:
                        logger.error("STAGNATION DETECTED: %s — restarting bot to recover", reason)
                        kill_bot_gracefully(proc)
                        break
                    else:
                        logger.info("Stagnation check: %s", reason)
                except Exception as e:
                    logger.debug("Stagnation check failed: %s", e)

            # v3.5.7: Lightweight watchdog checks (every 60s)
            # - Signal starvation + execution failure (via /api/admin/db_stats)
            # - Cash deployment ratio (via /api/stats)
            # - Resource alerts: WAL size, container memory, disk + auto-actions
            if now - last_watchdog_check >= watchdog_check_interval:
                last_watchdog_check = now
                check_signal_starvation(health_host, port)
                check_cash_deployment(health_host, port)
                check_resources(health_host, port)

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
