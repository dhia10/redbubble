"""
scheduler.py
=============
Runs the full automation pipeline on a daily schedule.

Two scheduling modes
--------------------
1. Built-in loop (schedule library): Run this script directly;
   it blocks and triggers the pipeline at the configured time each day.

2. External scheduler (Windows Task Scheduler / cron): Call
   ``python main.py --run-now`` from the task scheduler;
   this file exposes the pipeline runner as a callable function.

Usage
-----
    # Start the built-in daily loop (blocks):
    python modules/scheduler.py

    # Trigger immediately (for cron / Task Scheduler):
    python main.py --run-now
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class PipelineScheduler:
    """
    Wraps the daily pipeline execution with scheduling and error handling.

    Parameters
    ----------
    pipeline_fn  : Callable that runs one full pipeline iteration
    run_time     : Daily run time in "HH:MM" format (24-hour)
    """

    def __init__(
        self,
        pipeline_fn: Callable[[], None],
        run_time: str = "08:00",
    ) -> None:
        self.pipeline_fn = pipeline_fn
        self.run_time    = run_time

    # ------------------------------------------------------------------
    # Run pipeline with error handling
    # ------------------------------------------------------------------

    def _safe_run(self) -> None:
        """Execute the pipeline, catching and logging any uncaught error."""
        run_start = datetime.utcnow()
        logger.info(
            "=== Pipeline run starting at %s UTC ===",
            run_start.strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            self.pipeline_fn()
            elapsed = (datetime.utcnow() - run_start).seconds
            logger.info("=== Pipeline completed in %ds ===", elapsed)
        except Exception as exc:
            elapsed = (datetime.utcnow() - run_start).seconds
            logger.error(
                "=== Pipeline FAILED after %ds: %s ===", elapsed, exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Schedule-library based daily loop
    # ------------------------------------------------------------------

    def start_loop(self) -> None:
        """
        Block and run the pipeline at self.run_time every day.
        Requires the ``schedule`` library.
        """
        if not SCHEDULE_AVAILABLE:
            raise RuntimeError(
                "The 'schedule' library is not installed. "
                "Run: pip install schedule"
            )

        logger.info(
            "Scheduler started. Pipeline will run daily at %s UTC.",
            self.run_time,
        )
        schedule.every().day.at(self.run_time).do(self._safe_run)

        # Also run once immediately on startup (optional)
        logger.info("Running pipeline immediately on startup…")
        self._safe_run()

        while True:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds

    # ------------------------------------------------------------------
    # Run-now (for external schedulers)
    # ------------------------------------------------------------------

    def run_now(self) -> None:
        """Execute the pipeline once immediately."""
        self._safe_run()


# ---------------------------------------------------------------------------
# Windows Task Scheduler / cron helper
# ---------------------------------------------------------------------------

def print_windows_task_scheduler_command(
    python_exe: str = "python",
    script_path: str = r"C:\redbubble_automation\main.py",
    run_time: str = "08:00",
) -> None:
    """Print the schtasks command to register the daily Windows task."""
    h, m = run_time.split(":")
    cmd = (
        f'schtasks /Create /SC DAILY /TN "RedbubbleAutomation" '
        f'/TR "{python_exe} {script_path} --run-now" '
        f'/ST {h}:{m} /F'
    )
    print("\n=== Windows Task Scheduler Registration ===")
    print(cmd)
    print("\nTo remove: schtasks /Delete /TN RedbubbleAutomation /F")


def print_cron_entry(
    python_exe: str = "/usr/bin/python3",
    script_path: str = "/opt/redbubble_automation/main.py",
    run_time: str = "08:00",
    log_path: str = "/opt/redbubble_automation/logs/cron.log",
) -> None:
    """Print the crontab entry to run the pipeline daily."""
    h, m = run_time.split(":")
    entry = (
        f"{m} {h} * * * {python_exe} {script_path} --run-now "
        f">> {log_path} 2>&1"
    )
    print("\n=== Crontab Entry (add with: crontab -e) ===")
    print(entry)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print_windows_task_scheduler_command()
    print_cron_entry()

    print("\nTo start the built-in scheduler loop, run: python main.py")
    print("To run the pipeline once now:              python main.py --run-now")
