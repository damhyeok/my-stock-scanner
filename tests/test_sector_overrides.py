import unittest

from sector_overrides import NAME_SECTOR_OVERRIDES, SECTOR_STOCKS, override_sector


class SectorOverrideTests(unittest.TestCase):
    def test_requested_single_sector_names(self):
        self.assertEqual(override_sector("드림시큐리티", "기타"), "보안·양자")
        self.assertEqual(override_sector("HD현대일렉트릭", "에너지"), "전력")
        self.assertEqual(override_sector("멤레이비티", "기타"), "데이터센터")
        self.assertEqual(override_sector("삼성바이오로직스", "바이오"), "제약")
        self.assertEqual(override_sector("동국제약", "기타"), "제약")
        self.assertEqual(override_sector("아모레퍼시픽홀딩스", "기타"), "화장품")

    def test_duplicate_requests_keep_existing_sector(self):
        for name in ("삼성에스디에스", "대덕전자", "심텍", "후성", "솔브레인", "엠케이전자"):
            self.assertNotIn(name, NAME_SECTOR_OVERRIDES)
            self.assertEqual(override_sector(name, "기존 분류"), "기존 분류")

    def test_no_name_is_assigned_to_two_sectors(self):
        self.assertEqual(len(SECTOR_STOCKS), 24)
        all_names = [name.strip() for names in SECTOR_STOCKS.values() for name in names.split(",")]
        self.assertEqual(len(all_names), len(set(all_names)))

    def test_uncertain_names_are_not_guessed(self):
        self.assertNotIn("동국제강", NAME_SECTOR_OVERRIDES)
        self.assertNotIn("삼성바이오", NAME_SECTOR_OVERRIDES)


if __name__ == "__main__":
    unittest.main()
