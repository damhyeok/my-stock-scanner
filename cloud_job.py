import argparse
import asyncio
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from analysis_schedule import closest_full_analysis_cron
from market_strength import MarketStrengthAnalyzer
from program_ws_collector import ProgramTradeCollector
from sector_flow_collector import SectorFlowCollector
from crawler import StockCrawler
from intraday_relative_strength import IntradayRelativeStrengthScanner
from market_betting_engine.runtime import run_market_betting_analysis
from market_betting_engine.positions import Position, remove_position, upsert_position
from web_database import (
    build_web_database,
    compress_web_database,
    restore_working_database,
)
from storage_maintenance import run_storage_maintenance
from watchlist import WatchlistManager, refresh_watchlist


PROJECT_DIR = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
load_dotenv(PROJECT_DIR / ".env")


@contextmanager
def file_lock(lock_name):
    lock_path = PROJECT_DIR / lock_name
    lock_file = lock_path.open("w")
    try:
        if os.name != "nt":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name != "nt":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def run_command(command, env=None, check=True):
    print(f"[Cloud Job] 실행: {' '.join(command)}")
    return subprocess.run(
        command,
        cwd=PROJECT_DIR,
        env=env,
        check=check,
        text=True,
    )


def pull_latest():
    with file_lock(".cloud_git.lock"):
        run_command(["git", "pull", "--rebase", "--autostash", "origin", "main"])


def refresh_stock_analysis_timer():
    """Keep the installed Oracle full-analysis timer in sync with the repo.

    Pulling the repository does not update the copy already installed under
    /etc/systemd/system. Oracle Ubuntu images normally grant the ubuntu user
    passwordless sudo, so a regular analysis run can repair a stale timer
    without requiring another interactive SSH session.
    """

    if os.name == "nt":
        return False

    source = PROJECT_DIR / "deploy" / "oracle-cloud" / "stock-analysis.timer"
    target = Path("/etc/systemd/system/stock-analysis.timer")
    if not source.is_file():
        print(f"[Timer Refresh Warning] source timer not found: {source}")
        return False

    needs_install = True
    try:
        if target.is_file() and source.read_bytes() == target.read_bytes():
            needs_install = False
    except OSError as error:
        print(f"[Timer Refresh] installed timer comparison skipped: {error}")

    try:
        if needs_install:
            run_command(
                ["sudo", "-n", "install", "-m", "0644", str(source), str(target)]
            )
            run_command(["sudo", "-n", "systemctl", "daemon-reload"])
        run_command(
            ["sudo", "-n", "systemctl", "enable", "--now", "stock-analysis.timer"]
        )
        if needs_install:
            run_command(["sudo", "-n", "systemctl", "restart", "stock-analysis.timer"])
            print("[Timer Refresh] stock-analysis.timer updated and restarted.")
        else:
            print("[Timer Refresh] stock-analysis.timer is enabled and active.")
        return needs_install
    except (OSError, subprocess.CalledProcessError) as error:
        print(
            "[Timer Refresh Warning] automatic timer update failed; "
            f"the current analysis continues: {error}"
        )
        return False


def refresh_storage_maintenance_timer():
    """Install and enable the independent off-hours maintenance timer."""

    if os.name == "nt":
        return False

    timer_name = "storage-maintenance.timer"
    source = PROJECT_DIR / "deploy" / "oracle-cloud" / timer_name
    target = Path("/etc/systemd/system") / timer_name
    if not source.is_file():
        print(f"[Timer Refresh Warning] source timer not found: {source}")
        return False

    needs_install = True
    try:
        if target.is_file() and source.read_bytes() == target.read_bytes():
            needs_install = False
    except OSError as error:
        print(f"[Timer Refresh] installed timer comparison skipped: {error}")

    try:
        if needs_install:
            run_command(
                ["sudo", "-n", "install", "-m", "0644", str(source), str(target)]
            )
            run_command(["sudo", "-n", "systemctl", "daemon-reload"])
        run_command(["sudo", "-n", "systemctl", "enable", "--now", timer_name])
        if needs_install:
            run_command(["sudo", "-n", "systemctl", "restart", timer_name])
        return needs_install
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[Timer Refresh Warning] {timer_name} update failed: {error}")
        return False


def restart_trigger_server():
    """Reload the long-running trigger API after repository code updates."""

    if os.name == "nt":
        return False
    try:
        run_command(
            ["sudo", "-n", "systemctl", "try-restart", "stock-trigger.service"]
        )
        print("[Trigger Refresh] stock-trigger.service restarted.")
        return True
    except (OSError, subprocess.CalledProcessError) as error:
        print(
            "[Trigger Refresh Warning] trigger server restart failed; "
            f"the scheduled analysis continues: {error}"
        )
        return False


def push_reports(task_name):
    """Commit lightweight operational reports without rebuilding market data."""

    with file_lock(".cloud_git.lock"):
        reports_dir = PROJECT_DIR / "reports"
        if reports_dir.is_dir():
            run_command(["git", "add", "--", "reports/"])
        changed = run_command(["git", "diff", "--staged", "--quiet"], check=False)
        if changed.returncode == 0:
            print("[Cloud Job] GitHub에 반영할 변경사항이 없습니다.")
            return
        run_command(["git", "commit", "-m", f"Cloud update: {task_name} [skip ci]"])
        run_command(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        run_command(["git", "push", "origin", "main"])


def push_outputs(task_name):
    watchlist_summary = refresh_watchlist(PROJECT_DIR / "stock_data.db")
    print(
        "[Watchlist] "
        f"updated={watchlist_summary['updated']}, "
        f"failures={len(watchlist_summary['failures'])}"
    )
    build_web_database(PROJECT_DIR / "stock_data.db", PROJECT_DIR / "web_data.db")
    compress_web_database(
        PROJECT_DIR / "web_data.db", PROJECT_DIR / "web_data.db.gz"
    )
    push_reports(task_name)


def run_full_analysis(manual=False):
    now = datetime.now(KST)
    env = os.environ.copy()
    env.pop("ANALYSIS_RUN_MODE", None)
    env.pop("MARKET_STRENGTH_MODE", None)
    env.pop("MARKET_STRENGTH_REQUESTED_AT_KST", None)
    env.pop("GITHUB_EVENT_SCHEDULE", None)
    env.pop("SKIP_MARKET_STRENGTH", None)
    if manual:
        env["ANALYSIS_RUN_MODE"] = "full"
    else:
        scheduled_cron = closest_full_analysis_cron(now.hour, now.minute)
        env["GITHUB_EVENT_SCHEDULE"] = scheduled_cron
        if scheduled_cron == "30 2 * * 1-5":
            # 14시 누적 분석에서 API 조회 범위를 벗어나는 오전 값을 보존한다.
            env["MARKET_STRENGTH_MODE"] = "checkpoint"
        elif scheduled_cron != "50 0 * * 1-5":
            env["SKIP_MARKET_STRENGTH"] = "1"
    run_command([sys.executable, "main.py"], env=env)
    return scheduled_cron if not manual else None


def run_market_strength(analysis_type):
    analyzer = MarketStrengthAnalyzer(analysis_type=analysis_type)
    analyzer.run()


def run_collector(analysis_type):
    program_db = os.environ.get(
        "PROGRAM_SNAPSHOT_DB",
        str(PROJECT_DIR / "program_snapshots.db"),
    )
    collector = ProgramTradeCollector(analysis_type=analysis_type, db_path=program_db)
    asyncio.run(collector.collect())


def run_sector_flow():
    SectorFlowCollector(db_path=str(PROJECT_DIR / "stock_data.db")).run()


def run_bottom_model():
    env = os.environ.copy()
    env["ANALYSIS_RUN_MODE"] = "bottom_model"
    env.pop("GITHUB_EVENT_SCHEDULE", None)
    run_command([sys.executable, "main.py"], env=env)


def run_index_bar_collection():
    now = datetime.now(KST)
    if now.weekday() >= 5 or not (9 <= now.hour <= 15):
        print("[Index Bars] 정규장 수집 시간이 아니므로 건너뜁니다.")
        return
    crawler = StockCrawler(db_path=str(PROJECT_DIR / "stock_data.db"))
    cutoff_time = min(now.strftime("%H:%M"), "15:30")
    counts = IntradayRelativeStrengthScanner(crawler).collect_index_bars(
        trade_date=crawler.target_date,
        cutoff_time=cutoff_time,
    )
    print(
        f"[Index Bars] {crawler.target_date} {cutoff_time}: "
        + ", ".join(f"{name}={count}" for name, count in counts.items())
    )


def run_market_betting():
    run_market_betting_analysis(PROJECT_DIR / "stock_data.db")


def run_api_verification():
    """Capture sanitized live-session evidence without promoting any field."""

    output_dir = PROJECT_DIR / "reports" / "api_probes"
    result = run_command(
        [
            sys.executable,
            "-m",
            "market_betting_engine.api_probe",
            "--all-executable",
            "--provider",
            "KIS",
            "--ticker",
            os.environ.get("MARKET_BETTING_VERIFICATION_TICKER", "005930"),
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(output_dir / "api_probe_results.db"),
        ],
        check=False,
    )
    if result.returncode:
        print(
            f"[API Verification Warning] one or more probes failed "
            f"(exit={result.returncode}); the sanitized report was retained for review"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=[
            "full-analysis",
            "manual-analysis",
            "morning-collector",
            "morning-strength",
            "afternoon-collector",
            "afternoon-strength",
            "closing-collector",
            "closing-strength",
            "sector-flow",
            "bottom-model",
            "watchlist-add",
            "watchlist-remove",
            "intraday-rs-backfill",
            "index-bars",
            "market-betting",
            "api-verification",
            "position-upsert",
            "position-remove",
            "storage-maintenance",
        ],
    )
    parser.add_argument("--ticker")
    parser.add_argument("--name")
    parser.add_argument("--market-cap", type=int, default=0)
    parser.add_argument("--average-price", type=float)
    parser.add_argument("--quantity", type=float)
    parser.add_argument("--thesis-status", default="UNSPECIFIED")
    parser.add_argument("--thesis-note", default="")
    parser.add_argument("--invalidation-price", type=float)
    parser.add_argument("--trade-date")
    parser.add_argument("--session", default="정규장(16:00)")
    args = parser.parse_args()

    if args.task == "morning-collector":
        pull_latest()
        refresh_stock_analysis_timer()
        refresh_storage_maintenance_timer()
        run_collector("morning")
    elif args.task == "afternoon-collector":
        pull_latest()
        run_collector("afternoon")
    elif args.task == "closing-collector":
        pull_latest()
        run_collector("closing")
    elif args.task == "index-bars":
        with file_lock(".cloud_data.lock"):
            pull_latest()
            restore_working_database(
                PROJECT_DIR / "web_data.db", PROJECT_DIR / "stock_data.db"
            )
            run_index_bar_collection()
    elif args.task == "api-verification":
        with file_lock(".cloud_data.lock"):
            pull_latest()
            run_api_verification()
    else:
        with file_lock(".cloud_data.lock"):
            pull_latest()
            if args.task == "full-analysis":
                refresh_stock_analysis_timer()
                refresh_storage_maintenance_timer()
                restart_trigger_server()
            restore_working_database(
                PROJECT_DIR / "web_data.db", PROJECT_DIR / "stock_data.db"
            )
            if args.task in ("full-analysis", "manual-analysis"):
                scheduled_cron = run_full_analysis(manual=args.task == "manual-analysis")
                try:
                    run_market_betting()
                except Exception as error:
                    # The new decision-support engine must never prevent the
                    # existing scanner output from being published.
                    print(f"[Market Betting Warning] analysis failed; existing pipeline continues: {error}")
            elif args.task == "morning-strength":
                run_market_strength("morning")
            elif args.task == "afternoon-strength":
                run_market_strength("afternoon")
            elif args.task == "closing-strength":
                run_market_strength("closing")
                run_bottom_model()
            elif args.task == "sector-flow":
                run_sector_flow()
            elif args.task == "bottom-model":
                run_bottom_model()
            elif args.task == "watchlist-add":
                WatchlistManager(PROJECT_DIR / "stock_data.db").add(
                    args.ticker, args.name, args.market_cap
                )
            elif args.task == "watchlist-remove":
                WatchlistManager(PROJECT_DIR / "stock_data.db").remove(args.ticker)
            elif args.task == "position-upsert":
                upsert_position(
                    PROJECT_DIR / "stock_data.db",
                    Position(
                        ticker=args.ticker,
                        name=args.name or args.ticker,
                        average_price=args.average_price,
                        quantity=args.quantity,
                        thesis_status=args.thesis_status,
                        thesis_note=args.thesis_note,
                        invalidation_price=args.invalidation_price,
                    ),
                )
            elif args.task == "position-remove":
                remove_position(PROJECT_DIR / "stock_data.db", args.ticker)
            elif args.task == "intraday-rs-backfill":
                crawler = StockCrawler(db_path=str(PROJECT_DIR / "stock_data.db"))
                trade_date = args.trade_date or crawler.target_date
                crawler.target_date = trade_date
                result = IntradayRelativeStrengthScanner(crawler).run(
                    trade_date=trade_date,
                    session=args.session,
                )
                print(f"[Intraday RS Backfill] saved={len(result)}")
            elif args.task == "market-betting":
                run_market_betting()
            elif args.task == "storage-maintenance":
                allow_vacuum = datetime.now(KST).weekday() == 4
                with file_lock(".cloud_git.lock"):
                    report = run_storage_maintenance(
                        PROJECT_DIR, allow_vacuum=allow_vacuum
                    )
                print(
                    "[Storage Maintenance] "
                    f"disk={report['disk']['used_percent']:.2f}% "
                    f"status={report['disk']['status']}"
                )
                push_reports(args.task)
                return
            push_outputs(args.task)


if __name__ == "__main__":
    main()
