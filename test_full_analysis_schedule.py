import unittest

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


if __name__ == "__main__":
    unittest.main()
