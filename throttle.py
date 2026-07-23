"""CPU-aware throttle for background ingestion.

Two mechanisms, used together:

1. Process priority. The ingest process is dropped to BELOW_NORMAL so the OS
   scheduler hands the CPU to any foreground work that wants it.

2. Adaptive backoff. Before each unit of work, system CPU load is sampled. If
   it is above the target ceiling, the worker sleeps -- longer the more the
   ceiling is exceeded -- so ingestion settles into whatever slack is left.

The "slider" is `headroom_pct`: how much CPU to leave free. 50 means "never
push total system CPU past ~50%". The "switch" is `enabled`.

Decision logic (should_pause / backoff_seconds) is separated from the live
sampling loop so it can be tested without depending on real CPU load.
"""
from __future__ import annotations

import logging
import os
import sys
import time

logger = logging.getLogger("legalmind.throttle")

DEFAULT_HEADROOM = 50      # leave this % of CPU free
MIN_CEILING = 5            # never throttle below this ceiling
MAX_CEILING = 95           # above this, effectively unthrottled
SAMPLE_INTERVAL = 0.15     # seconds psutil averages CPU over
MAX_WAIT_PER_UNIT = 30.0   # cap the wait before one unit of work, seconds


class CpuThrottle:
    """Paces work to leave a target fraction of CPU free."""

    def __init__(self, headroom_pct: int = DEFAULT_HEADROOM,
                 enabled: bool = True, sample_interval: float = SAMPLE_INTERVAL):
        self.enabled = enabled
        self.sample_interval = sample_interval
        # The ceiling is the maximum system CPU% we allow before backing off.
        # "Leave 50% free" -> ceiling 50.
        self.ceiling = max(MIN_CEILING, min(MAX_CEILING, 100 - int(headroom_pct)))

    # ── pure decision logic (unit-testable, no live sampling) ────────────
    def should_pause(self, cpu_pct: float) -> bool:
        """True if measured system load is over the ceiling."""
        return self.enabled and cpu_pct > self.ceiling

    def backoff_seconds(self, cpu_pct: float) -> float:
        """How long to sleep given current load. Scales with the overage."""
        if not self.should_pause(cpu_pct):
            return 0.0
        over = (cpu_pct - self.ceiling) / 100.0     # 0..~1
        return min(2.0, 0.25 + over * 3.0)

    # ── side-effecting helpers (use real psutil / real clock) ────────────
    def set_background_priority(self) -> None:
        """Drop this process's scheduling priority. Best-effort."""
        if not self.enabled:
            return
        try:
            import psutil
            p = psutil.Process(os.getpid())
            if sys.platform == "win32":
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                p.nice(10)  # POSIX niceness
            logger.info("Ingest running at background priority "
                        "(CPU ceiling %d%%).", self.ceiling)
        except Exception as e:
            logger.warning("Could not lower process priority: %s", e)

    def restore_priority(self) -> None:
        """Return to normal priority (e.g. when a run finishes)."""
        try:
            import psutil
            p = psutil.Process(os.getpid())
            if sys.platform == "win32":
                p.nice(psutil.NORMAL_PRIORITY_CLASS)
            else:
                p.nice(0)
        except Exception:
            pass

    def wait_for_slack(self, max_wait: float = MAX_WAIT_PER_UNIT) -> float:
        """Block until system CPU is at or below the ceiling.

        Returns the seconds waited. Bounded by max_wait so a permanently busy
        machine still makes slow progress rather than stalling forever.
        """
        if not self.enabled:
            return 0.0
        try:
            import psutil
        except ImportError:
            return 0.0

        waited = 0.0
        while waited < max_wait:
            cpu = psutil.cpu_percent(interval=self.sample_interval)
            nap = self.backoff_seconds(cpu)
            if nap <= 0.0:
                return waited
            time.sleep(nap)
            waited += nap + self.sample_interval
        return waited


def from_settings(load_settings) -> CpuThrottle:
    """Build a CpuThrottle from the app's settings dict.

    load_settings is a zero-arg callable returning the settings dict (passed
    in rather than imported to keep this module free of a core.py dependency).
    Keys: 'ingest_background' (bool), 'ingest_cpu_headroom' (int %).
    """
    s = load_settings() or {}
    return CpuThrottle(
        headroom_pct=int(s.get("ingest_cpu_headroom", DEFAULT_HEADROOM)),
        enabled=bool(s.get("ingest_background", False)),
    )
