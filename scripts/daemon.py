"""Auto-healing daemon v3.5.15 — monitors bot process, restarts on crash. 24/7 reliable.

v3.5.15 fixes:
- check_signal_starvation now uses /api/stats (db_stats returns 403)
- Configurable thresholds via env vars: STAGNATION_THRESHOLD_MIN, STAGNATION_CHECK_INTERVAL
- Execution failure alert: 20+ signals, 0 trades, 0 open positions
- Container memory check uses dynamic container name

v3.5.14 fixes:
- CLOB WS data freshness check (last_msg_age_sec > 120 = stale → restart)
- Real TG alerts (sends to @polyclawchiper_bot on critical events)
- Fixed stagnation detector field names (signals_emitted, total_trades)
- CLOB WS error count tracking (auto-restart if >20 errors in 5 min)
- Dashboard status accuracy (writes status JSON for HTTP server)

v3.3.0 daemon improvements:
- NEVER give up: after crash loop threshold, switch to 5-min intervals (not exit)
- Deep health check: verify HTTP /api/health AND WS status via /api/stats
- Signal handling: graceful shutdown on SIGTERM/SIGINT
- Disk space check: warn if disk > 90% full
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

# ── TG Alert System ──────────────────────────────────────────────────────
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
TG_ALERT_COOLDOWN: dict[str, float] = {}  # alert_type → last_sent_timestamp


def send_tg_alert(alert_type: str, message: str, cooldown_sec: float = 300.0) -> None:
    """Send Telegram alert with cooldown to prevent spam."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.debug("TG: no token/chat_id configured, skipping alert: %s", alert_type)
        return

    now = time.time()
    last_sent = TG_ALERT_COOLDOWN.get(alert_type, 0)
    if now - last_sent < cooldown_sec:
        return  # Cooldown active

    TG_ALERT_COOLDOWN[alert_type] = now

    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": f"🔍 PolyClaw Alert\n\n{message}",
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("TG alert sent: %s", alert_type)
            else:
                logger.warning("TG alert failed: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("TG alert failed: %s", e)


# ── Health Checks ────────────────────────────────────────────────────────

def health_check_ok(host: str = "127.0.0.1", port: int = 8082, timeout: float = 3.0) -> bool:
    """Check if bot HTTP server is responding."""
    import urllib.request
    try:
        url = f"http://{host}:{port}/api/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def deep_health_check(host: str = "127.0.0.1", port: int = 8082, timeout: float = 5.0) -> tuple[bool, str]:
    """Deep health check — HTTP + WS status + data freshness."""
    import urllib.request
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

        clob_tokens = ws.get("clob_tokens", 0)
        if clob_tokens == 0:
            return False, "CLOB WS: 0 tokens tracked"

        # v3.5.14: Check data freshness — if last CLOB message > 120s ago, data is stale
        clob_last_msg = ws.get("clob_last_msg_sec", 0)
        if clob_last_msg > 120:
            return False, f"CLOB WS stale (no data for {int(clob_last_msg)}s)"

        return True, f"OK (clob={clob_tokens} tokens, uptime={stats.get('uptime_sec',0)}s)"

    except Exception as e:
        return False, f"check failed: {e}"


def check_disk_space(path: str = "/app", threshold: float = 0.90) -> tuple[bool, float]:
    try:
        usage = shutil.disk_usage(path)
        return usage.used / usage.total < threshold, usage.used / usage.total
    except Exception:
        return True, 0.0


def kill_bot_gracefully(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    logger.info("Sending SIGTERM to bot for graceful shutdown...")
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
        logger.info("Bot exited gracefully.")
    except subprocess.TimeoutExpired:
        logger.warning("Bot did not exit within %.1fs, forcing SIGKILL...", timeout)
        proc.kill()
        proc.wait()


# ── Stagnation Detector ──────────────────────────────────────────────────

class StagnationDetector:
    """Track state deltas to detect bot inactivity."""

    def __init__(self):
        self.history: dict[str, list[tuple[float, float]]] = {
            "bankroll": [],
            "trades": [],
            "signals": [],
        }
        self.last_stagnant_restart: float = 0.0
        self.stagnation_cooldown: float = 1800.0  # 30 min

    def record(self, stats: dict) -> None:
        now = time.time()
        # v3.5.15: Fixed field name — API returns 'trades' not 'total_trades'
        bankroll = stats.get("bankroll", 0)
        total_trades = stats.get("trades", 0)
        # Sum signals across all strategies
        total_signals = 0
        for s in stats.get("strategies", []):
            if isinstance(s, dict):
                total_signals += s.get("signals_emitted", 0)

        self.history["bankroll"].append((now, bankroll))
        self.history["trades"].append((now, total_trades))
        self.history["signals"].append((now, total_signals))

        cutoff = now - 7200
        for key in self.history:
            self.history[key] = [(ts, v) for ts, v in self.history[key] if ts > cutoff]

    def is_stagnant(self, stats: dict, threshold_min: int = 15) -> tuple[bool, str]:
        now = time.time()
        threshold = threshold_min * 60
        signal_threshold = max(threshold * 3, 300)  # 3x threshold or min 5 min

        for key in self.history:
            if len(self.history[key]) < 3:
                return False, "insufficient data"

        def _find_oldest_within(hist: list, window_sec: float) -> tuple[float, float] | None:
            """Find the oldest entry within window_sec from the latest entry."""
            if not hist:
                return None
            latest_ts = hist[-1][0]
            cutoff = latest_ts - window_sec
            # Find first entry at or after cutoff
            for ts, val in hist:
                if ts >= cutoff:
                    return (ts, val)
            return hist[0]  # fallback to oldest

        open_positions = stats.get("open_positions", [])
        has_open = len(open_positions) > 0

        # Check 1: Bankroll unchanged for threshold_min
        br_hist = self.history["bankroll"]
        br_old = _find_oldest_within(br_hist, threshold)
        if br_old and not has_open:
            if abs(br_hist[-1][1] - br_old[1]) < 0.01:
                return True, f"Bankroll unchanged for {threshold_min}m (stuck at ${br_hist[-1][1]:.2f})"

        # Check 2: No new trades for threshold_min (when bankroll > $30)
        bankroll = stats.get("bankroll", 0)
        tr_hist = self.history["trades"]
        tr_old = _find_oldest_within(tr_hist, threshold)
        if tr_old and bankroll > 30:
            if tr_hist[-1][1] == tr_old[1]:
                if has_open:
                    return True, f"No new trades for {threshold_min}m with {len(open_positions)} stuck open positions"
                return True, f"No new trades for {threshold_min}m (trades stuck at {int(tr_hist[-1][1])})"

        # Check 3: No new signals for signal_threshold
        sig_hist = self.history["signals"]
        sig_old = _find_oldest_within(sig_hist, signal_threshold)
        if sig_old:
            if sig_hist[-1][1] == sig_old[1]:
                return True, f"No new signals for {signal_threshold//60}m (strategies may be dead)"

        # Check 4: All strategies disabled
        disabled = stats.get("risk", {}).get("disabled_strategies", [])
        if len(disabled) >= 3:
            return True, f"All strategies disabled: {disabled}"

        # Check 5: 0 markets tracked
        markets = stats.get("markets", 0)
        if markets == 0:
            return True, "0 markets tracked (scanner dead?)"

        if has_open:
            return False, f"OK (have {len(open_positions)} open positions, signals flowing)"
        return False, "OK"

    def should_restart(self, stats: dict, threshold_min: int = 15) -> tuple[bool, str]:
        stagnant, reason = self.is_stagnant(stats, threshold_min)
        if not stagnant:
            return False, "OK"
        now = time.time()
        if now - self.last_stagnant_restart < self.stagnation_cooldown:
            remaining = int((self.stagnation_cooldown - (now - self.last_stagnant_restart)) // 60)
            return False, f"Stagnation detected but cooldown active ({remaining}m left)"
        self.last_stagnant_restart = now
        return True, reason


# ── CLOB WS Error Tracker ────────────────────────────────────────────────

class CLOBErrorTracker:
    """v3.5.14: Track CLOB WS reconnection errors — auto-restart if too many."""

    def __init__(self, max_errors: int = 15, window_sec: float = 300):
        self.max_errors = max_errors
        self.window_sec = window_sec
        self.errors: list[float] = []
        self.last_count = 0

    def check(self, host: str, port: int) -> tuple[bool, str]:
        """Check CLOB WS error count from logs. Returns (should_restart, reason)."""
        import urllib.request
        try:
            url = f"http://{host}:{port}/api/stats"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                stats = json.loads(resp.read().decode())

            ws = stats.get("ws_status", {})
            clob_errors = ws.get("clob_reconnect_count", 0)

            if clob_errors > self.last_count:
                new_errors = clob_errors - self.last_count
                now = time.time()
                self.errors.extend([now] * new_errors)
                self.last_count = clob_errors

            # Prune old errors
            cutoff = time.time() - self.window_sec
            self.errors = [t for t in self.errors if t > cutoff]

            if len(self.errors) >= self.max_errors:
                return True, f"CLOB WS {len(self.errors)} reconnects in {self.window_sec}s (threshold {self.max_errors})"

            return False, f"CLOB WS errors: {len(self.errors)}/{self.max_errors} in {self.window_sec}s"
        except Exception as e:
            return False, f"CLOB error check failed: {e}"


# ── Bot Process Management ───────────────────────────────────────────────

_shutdown_requested = False
_wal_alert_cooldown: float = 0.0
_disk_cleanup_cooldown: float = 0.0


def signal_handler(signum, frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_requested = True


def run_bot() -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = "/app/src"
    cmd = [sys.executable, "-m", "polyclaw_cipher_v3"]
    logger.info("Starting bot: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd="/app")

    def log_output():
        for line in iter(proc.stdout.readline, b""):
            sys.stdout.write(line.decode())
            sys.stdout.flush()

    import threading
    t = threading.Thread(target=log_output, daemon=True)
    t.start()
    return proc


# ── Watchdog Checks ──────────────────────────────────────────────────────

def check_signal_starvation(host: str, port: int) -> None:
    """v3.5.15: Check signal starvation + execution failure via /api/stats (db_stats returns 403)."""
    import urllib.request
    try:
        url = f"http://{host}:{port}/api/stats"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            stats = json.loads(resp.read().decode())

        # Extract signal/trade data from stats
        total_signals = 0
        per_strategy = {}
        for s in stats.get("strategies", []):
            if isinstance(s, dict):
                n = s.get("signals_emitted", 0)
                name = s.get("name", "unknown")
                per_strategy[name] = n
                total_signals += n

        total_trades = stats.get("trades", 0)
        open_positions = stats.get("open_positions", [])

        # Per-strategy starvation check
        for strat in ["momentum", "atomic_arb", "latency_arb", "resolution_snipe"]:
            n = per_strategy.get(strat, 0)
            if n == 0:
                logger.info("SignalCheck: %s emitted 0 signals this session", strat)

        # Execution failure: signals increasing but trades not increasing
        # v3.5.15: Track delta signals vs delta trades (not absolute 0)
        # If 20+ new signals but 0 new trades and 0 open positions → execution broken
        if not hasattr(check_signal_starvation, '_last_signals'):
            check_signal_starvation._last_signals = total_signals
            check_signal_starvation._last_trades = total_trades
        delta_signals = total_signals - check_signal_starvation._last_signals
        delta_trades = total_trades - check_signal_starvation._last_trades
        check_signal_starvation._last_signals = total_signals
        check_signal_starvation._last_trades = total_trades

        if delta_signals > 20 and delta_trades == 0 and len(open_positions) == 0:
            logger.error("SignalCheck: %d new signals but 0 new trades — execution issue?", delta_signals)
            send_tg_alert("execution_failure",
                f"🚨 Execution issue: {delta_signals} new signals, 0 new trades, 0 open positions\nPort: {port}\nRisk gate may be blocking all trades.",
                cooldown_sec=600)

        # High rejection proxy: if 50+ new signals but 0 new trades, likely high rejection
        if delta_signals > 50 and delta_trades == 0:
            logger.warning("SignalCheck: %d new signals, 0 new trades — check risk config", delta_signals)
            send_tg_alert("high_rejection",
                f"⚠️ Signal/trade imbalance: {delta_signals} new signals, 0 new trades\nPort: {port}\nRisk gate may be too tight.",
                cooldown_sec=600)

    except Exception as e:
        logger.debug("SignalCheck failed: %s", e)


def check_cash_deployment(host: str, port: int) -> None:
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
            logger.error("CashCheck: CRITICAL — cash=$%.2f (%.1f%%)", cash, cash_pct * 100)
        elif cash_pct < 0.05:
            logger.warning("CashCheck: over-deployed — cash=$%.2f (%.1f%%)", cash, cash_pct * 100)
    except Exception as e:
        logger.debug("CashCheck failed: %s", e)


def check_resources(host: str, port: int) -> None:
    import subprocess as sp
    global _wal_alert_cooldown, _disk_cleanup_cooldown
    now = time.time()

    # WAL file size
    try:
        wal_path = "/app/data/cipher_v3.db-wal"
        if os.path.exists(wal_path):
            wal_mb = os.path.getsize(wal_path) / (1024 * 1024)
            if wal_mb > 5.0 and now - _wal_alert_cooldown > 600:
                logger.warning("ResourceCheck: WAL file %.1fMB — triggering checkpoint", wal_mb)
                try:
                    import urllib.request
                    url = f"http://{host}:{port}/api/admin/wal_checkpoint"
                    req = urllib.request.Request(url, method="POST")
                    with urllib.request.urlopen(req, timeout=10.0) as resp:
                        json.loads(resp.read().decode())
                    _wal_alert_cooldown = now
                except Exception as e:
                    logger.error("ResourceCheck: WAL checkpoint failed: %s", e)
                    _wal_alert_cooldown = now
    except Exception:
        pass

    # Disk space
    disk_ok, disk_pct = check_disk_space()
    if not disk_ok:
        logger.error("ResourceCheck: Disk CRITICAL %.1f%%", disk_pct * 100)
        if now - _disk_cleanup_cooldown > 1800:
            logger.warning("ResourceCheck: Auto-cleanup — docker system prune")
            try:
                sp.run(["docker", "system", "prune", "-f"], capture_output=True, timeout=60.0)
                sp.run(["docker", "builder", "prune", "-f"], capture_output=True, timeout=60.0)
                _disk_cleanup_cooldown = now
            except Exception as e:
                logger.error("ResourceCheck: Auto-cleanup failed: %s", e)
                _disk_cleanup_cooldown = now
    elif disk_pct > 0.85:
        logger.warning("ResourceCheck: Disk high %.1f%%", disk_pct * 100)

    # Container memory (v3.5.15: dynamic container name)
    container_name = os.environ.get("CONTAINER_NAME", "polyclaw-cipher-v3")
    try:
        result = sp.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container_name],
            capture_output=True, text=True, timeout=5.0,
        )
        if result.returncode == 0 and result.stdout.strip():
            mem_str = result.stdout.strip().split(" / ")[0]
            if "MiB" in mem_str:
                mem_mb = float(mem_str.replace("MiB", "").strip())
            elif "GiB" in mem_str:
                mem_mb = float(mem_str.replace("GiB", "").strip()) * 1024
            else:
                mem_mb = 0
            if mem_mb > 800:
                logger.error("ResourceCheck: Container memory %.0fMB — OOM imminent", mem_mb)
                send_tg_alert("high_memory",
                    f"🚨 Container memory critical: {mem_mb:.0f}MB / 1024MB\nOOM imminent — investigate now!",
                    cooldown_sec=300)
            elif mem_mb > 600:
                logger.warning("ResourceCheck: Container memory %.0fMB — high", mem_mb)
    except Exception:
        pass


# ── Main Daemon Loop ─────────────────────────────────────────────────────

def main() -> None:
    global _shutdown_requested

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    port = int(os.environ.get("HTTP_PORT", "8082"))
    health_host = "127.0.0.1"
    crash_loop_threshold = 10
    long_interval = 300
    restart_history: list[float] = []
    backoff_delays = [5, 10, 20, 40, 80, 160, 300]

    Path("data").mkdir(exist_ok=True)
    Path("data/heartbeat.json").write_text('{"heartbeat": ' + str(time.time()) + '}')

    deep_check_interval = 60
    last_deep_check = 0.0
    stagnation_detector = StagnationDetector()
    last_stagnation_check = 0.0
    last_watchdog_check = 0.0

    # v3.5.15: Configurable thresholds via env vars (for testing)
    stagnation_threshold = int(os.environ.get("STAGNATION_THRESHOLD_MIN", "15"))
    stagnation_check_interval = int(os.environ.get("STAGNATION_CHECK_INTERVAL", "120"))
    watchdog_check_interval = int(os.environ.get("WATCHDOG_CHECK_INTERVAL", "60"))

    # v3.5.14: CLOB error tracker
    clob_error_tracker = CLOBErrorTracker(max_errors=15, window_sec=300)
    clob_check_interval = 30  # Check every 30s
    last_clob_check = 0.0

    bot_label = os.environ.get("BOT_LABEL", f"port-{port}")

    logger.info("Daemon v3.5.15 started — bot port=%d, label=%s", port, bot_label)
    logger.info("TG alerts: %s", "ENABLED" if TG_BOT_TOKEN else "DISABLED (no token)")
    logger.info("Stagnation threshold: %dm, check interval: %ds", stagnation_threshold, stagnation_check_interval)

    send_tg_alert("daemon_start",
        f"✅ Daemon started for {bot_label} (port {port})\nMonitoring active: CLOB WS, stagnation, signals, resources",
        cooldown_sec=0)

    while not _shutdown_requested:
        proc = run_bot()
        start_time = time.time()
        uptime_threshold = 3600
        consecutive_restart_idx = 0
        last_deep_check = time.time()
        last_stagnation_check = time.time()
        last_watchdog_check = time.time()
        last_clob_check = time.time()

        # v3.5.14: Alert on bot start
        send_tg_alert("bot_start",
            f"🔄 Bot started: {bot_label} (port {port})",
            cooldown_sec=60)

        while proc.poll() is None and not _shutdown_requested:
            time.sleep(10)
            now = time.time()

            # Skip health check during 30s startup grace
            if now - start_time < 30:
                continue

            # Basic HTTP health check (every 10s)
            if not health_check_ok(health_host, port):
                logger.warning("Basic health check failed — restarting bot")
                send_tg_alert("health_fail",
                    f"⚠️ Health check failed: {bot_label} (port {port})\nHTTP server not responding — restarting bot",
                    cooldown_sec=120)
                kill_bot_gracefully(proc)
                break

            # v3.5.14: CLOB WS error tracking (every 30s)
            if now - last_clob_check >= clob_check_interval:
                last_clob_check = now
                should_restart, reason = clob_error_tracker.check(health_host, port)
                if should_restart:
                    logger.error("CLOB WS error threshold exceeded: %s — restarting bot", reason)
                    send_tg_alert("clob_ws_errors",
                        f"🔴 CLOB WebSocket critical: {reason}\nBot: {bot_label} (port {port})\nAuto-restarting to recover.",
                        cooldown_sec=300)
                    kill_bot_gracefully(proc)
                    break

            # Deep health check (every 60s)
            if now - last_deep_check >= deep_check_interval:
                last_deep_check = now
                healthy, reason = deep_health_check(health_host, port)
                if not healthy:
                    logger.warning("Deep health check failed: %s — restarting bot", reason)
                    send_tg_alert("deep_health_fail",
                        f"🔴 Deep health check failed: {reason}\nBot: {bot_label} (port {port})\nAuto-restarting to recover.",
                        cooldown_sec=300)
                    kill_bot_gracefully(proc)
                    break

                disk_ok, disk_pct = check_disk_space()
                if not disk_ok:
                    logger.error("Disk space CRITICAL: %.1f%%", disk_pct * 100)
                    send_tg_alert("disk_full",
                        f"🚨 Disk space CRITICAL: {disk_pct*100:.1f}% full\nBot: {bot_label}",
                        cooldown_sec=600)

            # Stagnation detection (every 2 min)
            if now - last_stagnation_check >= stagnation_check_interval:
                last_stagnation_check = now
                try:
                    import urllib.request
                    url = f"http://{health_host}:{port}/api/stats"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        stats = json.loads(resp.read().decode())
                    stagnation_detector.record(stats)
                    should_restart, reason = stagnation_detector.should_restart(stats, threshold_min=stagnation_threshold)
                    if should_restart:
                        logger.error("STAGNATION DETECTED: %s — restarting bot", reason)
                        send_tg_alert("stagnation",
                            f"🟡 Stagnation detected: {reason}\nBot: {bot_label} (port {port})\nAuto-restarting to recover.",
                            cooldown_sec=600)
                        kill_bot_gracefully(proc)
                        break
                    else:
                        logger.info("Stagnation check: %s", reason)
                except Exception as e:
                    logger.debug("Stagnation check failed: %s", e)

            # Watchdog checks (every 60s)
            if now - last_watchdog_check >= watchdog_check_interval:
                last_watchdog_check = now
                check_signal_starvation(health_host, port)
                check_cash_deployment(health_host, port)
                check_resources(health_host, port)

        if _shutdown_requested:
            logger.info("Graceful shutdown requested — exiting bot")
            kill_bot_gracefully(proc)
            break

        exit_code = proc.returncode
        uptime = time.time() - start_time

        if uptime > uptime_threshold:
            consecutive_restart_idx = 0
            logger.info("Bot had stable uptime=%.0fs, resetting backoff", uptime)
        else:
            consecutive_restart_idx = min(consecutive_restart_idx + 1, len(backoff_delays) - 1)

        now = time.time()
        restart_history = [t for t in restart_history if now - t < 3600]
        restart_history.append(now)
        restarts_this_hour = len(restart_history)

        if restarts_this_hour > crash_loop_threshold:
            logger.error("CRASH LOOP: %d restarts/hour — switching to %ds intervals", restarts_this_hour, long_interval)
            send_tg_alert("crash_loop",
                f"🚨 CRASH LOOP: {restarts_this_hour} restarts in 1h\nBot: {bot_label} (port {port})\nSwitching to 5-min intervals (not giving up).",
                cooldown_sec=900)
            delay = long_interval
        else:
            delay = backoff_delays[consecutive_restart_idx]

        logger.warning("Bot crashed (exit=%d, uptime=%.0fs) — restart in %ds", exit_code, uptime, delay)
        send_tg_alert("bot_crash",
            f"💥 Bot crashed: exit={exit_code}, uptime={uptime:.0f}s\nBot: {bot_label} (port {port})\nRestarting in {delay}s.",
            cooldown_sec=120)

        time.sleep(delay)

    send_tg_alert("daemon_stop",
        f"🛑 Daemon stopped: {bot_label} (port {port})",
        cooldown_sec=0)
    logger.info("Daemon stopped gracefully")


if __name__ == "__main__":
    main()
