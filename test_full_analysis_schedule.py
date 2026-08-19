import unittest
from pathlib import Path

from analysis_schedule import FULL_ANALYSIS_SESSION_BY_CRON, closest_full_analysis_cron


class FullAnalysisScheduleTest(unittest.TestCase):
    CASES = [
        (9, 30, "30 0 * * 1-5", "장중(09:30)"),
        (9, 50, "50 0 * * 1-5", "장중(09:50)"),
        (10, 50, "50 1 * * 1-5", "장중(10:50)"),
        (11, 30, "30 2 * * 1-5", "장중(11:30)"),
        (11, 50, "50 2 * * 1-5", "장중(11:50)"),
        (12, 50, "50 3 * * 1-5", "장중(12:50)"),
        (14, 0, "0 5 * * 1-5", "장중(14:00)"),
        (14, 50, "50 5 * * 1-5", "장중(14:50)"),
        (16, 0, "0 7 * * 1-5", "정규장(16:00)"),
    ]

    def test_cloud_schedule_and_crawler_session_labels_match(self):
        for hour, minute, expected_cron, expected_session in self.CASES:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                self.assertEqual(closest_full_analysis_cron(hour, minute), expected_cron)
                self.assertEqual(FULL_ANALYSIS_SESSION_BY_CRON[expected_cron], expected_session)

    def test_each_analysis_slot_has_its_own_oracle_timer(self):
        timer_dir = Path(__file__).parent / "deploy" / "oracle-cloud"
        timer_files = sorted(timer_dir.glob("stock-analysis-[0-9][0-9][0-9][0-9].timer"))
        self.assertEqual(len(timer_files), len(self.CASES))

        expected_times = {f"{hour:02d}:{minute:02d}:00" for hour, minute, _, _ in self.CASES}
        configured_times = set()
        for timer_file in timer_files:
            content = timer_file.read_text(encoding="utf-8")
            self.assertIn("Unit=stock-scanner@full-analysis.service", content)
            self.assertIn("Persistent=true", content)
            for expected_time in expected_times:
                if expected_time in content:
                    configured_times.add(expected_time)
        self.assertEqual(configured_times, expected_times)


if __name__ == "__main__":
    unittest.main()
