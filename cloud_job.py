import argparse
import asyncio
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from market_strength import MarketStrengthAnalyzer
from program_ws_collector import ProgramTradeCollector


PROJECT_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
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


def push_outputs(task_name):
    with file_lock(".cloud_git.lock"):
        run_command(["git", "add", "--", "stock_data.db"])
        reports_dir = PROJECT_DIR / "reports"
        if reports_dir.is_dir():
            run_command(["git", "add", "--", "reports/"])
        changed = run_command(["git", "diff", "--staged", "--quiet"], check=False)
        if changed.returncode == 0:
            print("[Cloud Job] GitHub에 반영할 변경사항이 없습니다.")
            return
        run_command(
            [
                "git",
                "commit",
                "-m",
                f"Cloud update: {task_name} [skip ci]",
            ]
        )
        run_command(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        run_command(["git", "push", "origin", "main"])


def run_full_analysis():
    now = datetime.now(KST)
    env = os.environ.copy()
    env.pop("ANALYSIS_RUN_MODE", None)
    env.pop("MARKET_STRENGTH_MODE", None)
    env.pop("MARKET_STRENGTH_REQUESTED_AT_KST", None)
    if now.hour < 12:
        env["GITHUB_EVENT_SCHEDULE"] = "30 0 * * 1-5"
    else:
        env["GITHUB_EVENT_SCHEDULE"] = "0 7 * * 1-5"
    run_command([sys.executable, "main.py"], env=env)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=[
            "full-analysis",
            "morning-collector",
            "morning-strength",
            "closing-collector",
            "closing-strength",
        ],
    )
    args = parser.parse_args()

    if args.task == "morning-collector":
        pull_latest()
        run_collector("morning")
    elif args.task == "closing-collector":
        pull_latest()
        run_collector("closing")
    else:
        with file_lock(".cloud_data.lock"):
            pull_latest()
            if args.task == "full-analysis":
                run_full_analysis()
            elif args.task == "morning-strength":
                run_market_strength("morning")
            elif args.task == "closing-strength":
                run_market_strength("closing")
            push_outputs(args.task)


if __name__ == "__main__":
    main()
