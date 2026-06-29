"""Auto-healing daemon v3.5.17 — trigger-based pipeline health.

Design:
- CRITICAL (10s): bot crash, HTTP dead, CLOB WS dead → immediate restart + alert
- TRIGGER (60s poll): 15 min no new trades OR 15 min bankroll unchanged → pipeline trace
- PIPELINE TRACE: 9-step checklist, combined alert (30 min cooldown)
- SILENT: bot trading normally → daemon stays quiet

Replaces v3.5.15's polling-everything approach with event-driven design.
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

# ── Config ───────────────────────────────────────────────────────────────
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
PORT = int(os.environ.get("HTTP_PORT", "8082"))
HEALTH_HOST = "127.0.0.1"
BOT_LABEL = os.environ.get("BOT_LABEL", f"port-{PORT}")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "polyclaw-cipher-v3")

TRIGGER_MINUTES = 15          # no new trades / bankroll unchanged threshold
TRIGGER_POLL_SEC = 60         # how often to check triggers
CRITICAL_CHECK_SEC = 10       # how often to check critical health
PIPELINE_ALERT_COOLDOWN = 1800  # 30 min between combined pipeline alerts
STARTUP_GRACE_SEC = 30        # skip checks during startup

# ── TG Alert ─────────────────────────────────────────────────────────────
_ALERT_COOLDOWNS: dict[str, float] = {}


def send_tg(alert_type: str, message: str, cooldown: float = 300.0) -> None:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    now = time.time()
    last = _ALERT_COOLDOWNS.get(alert_type, 0)
    if now - last < cooldown:
        return
    _ALERT_COOLDOWNS[alert_type] = now
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": f"🔍 {BOT_LABEL}\n\n{message}",
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("TG alert sent: %s", alert_type)
    except Exception as e:
        logger.warning("TG alert failed: %s", e)


# ── API helpers ──────────────────────────────────────────────────────────

def _get(path: str, timeout: float = 5.0) -> dict | None:
    import urllib.request
    try:
        url = f"http://{HEALTH_HOST}:{PORT}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _health_ok() -> bool:
    import urllib.request
    try:
        url = f"http://{HEALTH_HOST}:{PORT}/api/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


# ── Critical Check (every 10s) ───────────────────────────────────────────

def critical_check() -> tuple[bool, str]:
    """Returns (ok, reason). If not ok → restart bot immediately."""
    # 1. HTTP alive
    if not _health_ok():
        return False, "HTTP server not responding"
    # 2. CLOB WS alive + fresh
    stats = _get("/api/stats")
    if stats is None:
        return False, "/api/stats not responding"
    ws = stats.get("ws_status", {})
    if not ws.get("clob_connected", False):
        return False, "CLOB WS disconnected"
    clob_tokens = ws.get("clob_tokens", 0)
    if clob_tokens == 0:
        return False, "CLOB WS: 0 tokens tracked"
    clob_last_msg = ws.get("clob_last_msg_sec", 0)
    if clob_last_msg > 120:
        return False, f"CLOB WS stale ({int(clob_last_msg)}s no data)"
    return True, "OK"


# ── 9-Step Pipeline Trace ────────────────────────────────────────────────

def pipeline_trace(stats: dict, prev_state: dict) -> tuple[list[str], list[str]]:
    """Run 9-step pipeline health check.
    
    Returns (issues, passes) — lists of diagnostic messages.
    """
    issues = []
    passes = []

    # Step 1: Config loaded — strategies not empty
    strategies = []
    for s in stats.get("strategies", []):
        if isinstance(s, dict):
            strategies.append(s.get("name", "?"))
    if not strategies:
        issues.append("⚠️ Step 1: Strategies loaded = [] — config broken!")
    else:
        passes.append(f"✅ Step 1: Strategies = {strategies}")

    # Step 2: Markets scanned
    markets = stats.get("markets", 0)
    if markets == 0:
        issues.append("⚠️ Step 2: 0 markets — scanner dead?")
    else:
        # Check category distribution
        cats = stats.get("categories", {})
        if cats:
            cat_summary = ", ".join(f"{k}={v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])[:5])
            passes.append(f"✅ Step 2: {markets} markets ({cat_summary})")
        else:
            passes.append(f"✅ Step 2: {markets} markets")

    # Step 3: CLOB WS alive
    ws = stats.get("ws_status", {})
    clob_tokens = ws.get("clob_tokens", 0)
    clob_last = ws.get("clob_last_msg_sec", 999)
    if clob_tokens > 0 and clob_last < 120:
        passes.append(f"✅ Step 3: CLOB {clob_tokens} tokens, last msg {int(clob_last)}s ago")
    else:
        issues.append(f"⚠️ Step 3: CLOB WS issue — tokens={clob_tokens}, last_msg={int(clob_last)}s")

    # Step 4: Binance WS alive
    binance_ok = ws.get("binance_connected", False)
    if binance_ok:
        passes.append("✅ Step 4: Binance WS connected")
    else:
        issues.append("⚠️ Step 4: Binance WS disconnected")

    # Step 5: Eval cycle running
    mom = stats.get("momentum_debug", {})
    evaluated = mom.get("evaluated", 0)
    prev_evaluated = prev_state.get("evaluated", 0)
    if evaluated > prev_evaluated:
        passes.append(f"✅ Step 5: Eval running ({evaluated} total, +{evaluated - prev_evaluated} since last check)")
    elif evaluated > 0:
        issues.append(f"⚠️ Step 5: Eval count stuck at {evaluated} — strategy loop frozen?")
    else:
        issues.append("⚠️ Step 5: 0 evaluations — strategy not running")

    # Step 6: Signals generating
    mom_signals = mom.get("signals", 0)
    total_signals = sum(s.get("signals_emitted", 0) for s in stats.get("strategies", []) if isinstance(s, dict))
    
    if total_signals > 0 or mom_signals > 0:
        passes.append(f"✅ Step 6: {total_signals} signals emitted ({mom_signals} from momentum)")
    else:
        # Diagnostic: why 0 signals?
        eval_count = mom.get("evaluated", 0)
        if eval_count > 50:
            filters = [
                ("price_filtered", mom.get("price_filtered", 0)),
                ("no_change", mom.get("no_change", 0)),
                ("cat_filtered", mom.get("cat_filtered", 0)),
                ("cooldown", mom.get("cooldown", 0)),
                ("max_pos", mom.get("max_pos", 0)),
                ("low_conf", mom.get("low_conf", 0)),
                ("one_per_mkt", mom.get("one_per_mkt", 0)),
                ("vol_filtered", mom.get("vol_filtered", 0)),
            ]
            total_filtered = sum(v for _, v in filters)
            dominant = sorted(filters, key=lambda x: -x[1])[0]
            pct = dominant[1] / max(total_filtered, 1) * 100
            issues.append(
                f"⚠️ Step 6: 0 signals from {eval_count} evals — "
                f"dominant filter: {dominant[0]} ({pct:.0f}%)"
            )
        else:
            issues.append(f"⚠️ Step 6: 0 signals (only {eval_count} evals — still warming up?)")

    # Step 7: Risk gate passing
    total_trades = stats.get("trades", 0)
    open_positions = stats.get("open_positions", [])
    disabled = stats.get("risk", {}).get("disabled_strategies", [])
    
    if disabled and len(disabled) >= 2:
        issues.append(f"⚠️ Step 7: {len(disabled)} strategies disabled by risk: {disabled}")
    elif total_signals > 20 and total_trades == 0 and not open_positions:
        issues.append(f"⚠️ Step 7: {total_signals} signals but 0 trades — risk gate blocking everything")
    else:
        passes.append(f"✅ Step 7: Risk gate OK ({total_trades} trades, {len(open_positions)} open, disabled={disabled})")

    # Step 8: Positions managed
    if open_positions:
        passes.append(f"✅ Step 8: {len(open_positions)} open positions tracked")
    elif total_trades > 0:
        passes.append("✅ Step 8: No open positions (all closed)")
    else:
        passes.append("✅ Step 8: No positions yet (bot may be starting)")

    # Step 9: Bankroll moving
    bankroll = stats.get("bankroll", 0)
    prev_bankroll = prev_state.get("bankroll", bankroll)
    if abs(bankroll - prev_bankroll) > 0.01:
        passes.append(f"✅ Step 9: Bankroll ${bankroll:.2f} (was ${prev_bankroll:.2f})")
    else:
        issues.append(f"⚠️ Step 9: Bankroll stuck at ${bankroll:.2f}")

    return issues, passes


# ── Trigger Tracker ──────────────────────────────────────────────────────

class TriggerTracker:
    """Track when to trigger pipeline trace."""
    
    def __init__(self):
        self.last_trade_count = -1
        self.last_trade_time = time.time()
        self.last_bankroll = -1.0
        self.last_bankroll_time = time.time()
        self.prev_pipeline_state: dict = {}
        self.last_pipeline_alert = 0.0
    
    def update(self, stats: dict) -> tuple[bool, str]:
        """Check if pipeline trace should run. Returns (should_trace, reason)."""
        now = time.time()
        
        # Track trade count
        trades = stats.get("trades", 0)
        if self.last_trade_count < 0:
            self.last_trade_count = trades
        elif trades > self.last_trade_count:
            self.last_trade_count = trades
            self.last_trade_time = now
        
        # Track bankroll
        bankroll = stats.get("bankroll", 0)
        if self.last_bankroll < 0:
            self.last_bankroll = bankroll
        elif abs(bankroll - self.last_bankroll) > 0.01:
            self.last_bankroll = bankroll
            self.last_bankroll_time = now
        
        # Check triggers
        no_trade_sec = now - self.last_trade_time
        no_bankroll_sec = now - self.last_bankroll_time
        
        if no_trade_sec >= TRIGGER_MINUTES * 60:
            return True, f"no new trades for {int(no_trade_sec // 60)}m"
        if no_bankroll_sec >= TRIGGER_MINUTES * 60:
            return True, f"bankroll unchanged for {int(no_bankroll_sec // 60)}m"
        
        return False, ""
    
    def run_trace(self, stats: dict) -> None:
        """Run pipeline trace and send combined alert if needed."""
        issues, passes = pipeline_trace(stats, self.prev_pipeline_state)
        
        # Update prev state for next trace
        self.prev_pipeline_state = {
            "evaluated": stats.get("momentum_debug", {}).get("evaluated", 0),
            "bankroll": stats.get("bankroll", 0),
        }
        
        if issues:
            now = time.time()
            if now - self.last_pipeline_alert < PIPELINE_ALERT_COOLDOWN:
                remaining = int((PIPELINE_ALERT_COOLDOWN - (now - self.last_pipeline_alert)) // 60)
                logger.info("Pipeline: %d issues found but alert cooldown (%dm left)", len(issues), remaining)
                return
            
            self.last_pipeline_alert = now
            
            # Build combined alert
            msg_lines = [f"📋 Pipeline Trace — {int(time.time())} issues found:\n"]
            for issue in issues:
                msg_lines.append(issue)
            msg_lines.append(f"\n✅ {len(passes)} checks passed:")
            for p in passes:
                msg_lines.append(p)
            
            alert_msg = "\n".join(msg_lines)
            logger.warning("Pipeline trace triggered:\n%s", alert_msg)
            send_tg("pipeline_trace", alert_msg, cooldown=PIPELINE_ALERT_COOLDOWN)
        else:
            logger.info("Pipeline trace: all 9 steps passed ✅")


# ── Resource Check ───────────────────────────────────────────────────────

_wal_alert_cooldown = 0.0
_disk_cleanup_cooldown = 0.0


def check_resources() -> None:
    global _wal_alert_cooldown, _disk_cleanup_cooldown
    now = time.time()
    
    # WAL file
    wal_path = "/app/data/cipher_v3.db-wal"
    if os.path.exists(wal_path):
        wal_mb = os.path.getsize(wal_path) / (1024 * 1024)
        if wal_mb > 5.0 and now - _wal_alert_cooldown > 600:
            logger.warning("WAL file %.1fMB — checkpointing", wal_mb)
            try:
                stats = _get("/api/admin/wal_checkpoint", timeout=10.0)
                _wal_alert_cooldown = now
            except Exception:
                _wal_alert_cooldown = now
    
    # Disk space
    try:
        usage = shutil.disk_usage("/app")
        disk_pct = usage.used / usage.total
        if disk_pct > 0.90:
            logger.error("Disk CRITICAL: %.1f%%", disk_pct * 100)
            if now - _disk_cleanup_cooldown > 1800:
                subprocess.run(["docker", "system", "prune", "-f"], capture_output=True, timeout=60)
                subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True, timeout=60)
                _disk_cleanup_cooldown = now
        elif disk_pct > 0.85:
            logger.warning("Disk high: %.1f%%", disk_pct * 100)
    except Exception:
        pass
    
    # Container memory
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CONTAINER_NAME],
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
                logger.error("Memory CRITICAL: %.0fMB", mem_mb)
                send_tg("high_memory", f"🚨 Memory: {mem_mb:.0f}MB / 1024MB — OOM imminent!", cooldown=300)
            elif mem_mb > 600:
                logger.warning("Memory high: %.0fMB", mem_mb)
    except Exception:
        pass


# ── Bot Process Management ───────────────────────────────────────────────

_shutdown_requested = False


def signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Received signal %s — shutting down", signal.Signals(signum).name)


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


def kill_bot(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    logger.info("Graceful shutdown bot...")
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ── Main Loop ────────────────────────────────────────────────────────────

def main() -> None:
    global _shutdown_requested
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    crash_loop_threshold = 10
    long_interval = 300
    restart_history: list[float] = []
    backoff_delays = [5, 10, 20, 40, 80, 160, 300]
    uptime_threshold = 3600
    
    Path("data").mkdir(exist_ok=True)
    
    trigger = TriggerTracker()
    
    logger.info("Daemon v3.5.17 started — port=%d, label=%s", PORT, BOT_LABEL)
    logger.info("Triggers: %dm no-trades / %dm no-bankroll → pipeline trace", TRIGGER_MINUTES, TRIGGER_MINUTES)
    logger.info("TG alerts: %s", "ENABLED" if TG_BOT_TOKEN else "DISABLED")
    
    send_tg("daemon_start",
        f"✅ Daemon v3.5.17 started\n{BOT_LABEL} (port {PORT})\nTrigger: {TRIGGER_MINUTES}m → pipeline trace",
        cooldown=0)
    
    while not _shutdown_requested:
        proc = run_bot()
        start_time = time.time()
        consecutive_restart_idx = 0
        last_critical = time.time()
        last_trigger_check = time.time()
        last_resource = time.time()
        
        # Reset trigger tracker on new bot start
        trigger = TriggerTracker()
        
        send_tg("bot_start", f"🔄 Bot started: {BOT_LABEL} (port {PORT})", cooldown=60)
        
        while proc.poll() is None and not _shutdown_requested:
            time.sleep(CRITICAL_CHECK_SEC)
            now = time.time()
            
            # Skip during startup grace
            if now - start_time < STARTUP_GRACE_SEC:
                continue
            
            # ── CRITICAL (every 10s) ──
            ok, reason = critical_check()
            if not ok:
                logger.error("CRITICAL: %s — restarting bot", reason)
                send_tg("critical", f"🔴 Critical: {reason}\n{BOT_LABEL} (port {PORT})\nAuto-restart.", cooldown=120)
                kill_bot(proc)
                break
            
            # ── TRIGGER CHECK (every 60s) ──
            if now - last_trigger_check >= TRIGGER_POLL_SEC:
                last_trigger_check = now
                stats = _get("/api/stats")
                if stats:
                    should_trace, trace_reason = trigger.update(stats)
                    if should_trace:
                        logger.info("Pipeline trace triggered: %s", trace_reason)
                        trigger.run_trace(stats)
                    else:
                        logger.debug("Triggers OK — trades %dm ago, bankroll %dm ago",
                                     int((now - trigger.last_trade_time) // 60),
                                     int((now - trigger.last_bankroll_time) // 60))
            
            # ── RESOURCES (every 5 min) ──
            if now - last_resource >= 300:
                last_resource = now
                check_resources()
        
        if _shutdown_requested:
            kill_bot(proc)
            break
        
        # ── Crash recovery ──
        exit_code = proc.returncode
        uptime = time.time() - start_time
        
        if uptime > uptime_threshold:
            consecutive_restart_idx = 0
            logger.info("Stable uptime=%.0fs, resetting backoff", uptime)
        else:
            consecutive_restart_idx = min(consecutive_restart_idx + 1, len(backoff_delays) - 1)
        
        now = time.time()
        restart_history = [t for t in restart_history if now - t < 3600]
        restart_history.append(now)
        restarts_this_hour = len(restart_history)
        
        if restarts_this_hour > crash_loop_threshold:
            logger.error("CRASH LOOP: %d restarts/hour — 5min intervals", restarts_this_hour)
            send_tg("crash_loop",
                f"🚨 Crash loop: {restarts_this_hour} restarts/hour\n{BOT_LABEL} (port {PORT})\n5-min intervals.",
                cooldown=900)
            delay = long_interval
        else:
            delay = backoff_delays[consecutive_restart_idx]
        
        logger.warning("Bot crashed (exit=%d, uptime=%.0fs) — restart in %ds", exit_code, uptime, delay)
        send_tg("bot_crash",
            f"💥 Bot crashed: exit={exit_code}, uptime={uptime:.0f}s\n{BOT_LABEL} (port {PORT})\nRestart in {delay}s.",
            cooldown=120)
        
        time.sleep(delay)
    
    send_tg("daemon_stop", f"🛑 Daemon stopped: {BOT_LABEL} (port {PORT})", cooldown=0)
    logger.info("Daemon stopped")


if __name__ == "__main__":
    main()
