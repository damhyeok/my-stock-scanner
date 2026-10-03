import unittest

from sector_overrides import NAME_SECTOR_OVERRIDES, SECTOR_STOCKS, override_sector


class SectorOverrideTests(unittest.TestCase):
    def test_requested_single_sector_names(self):
        self.assertEqual(override_sector("드림시큐리티", "기타"), "보안·양자")
        self.assertEqual(override_sector("HD현대일렉트릭", "에너지"), "전력")
        self.assertEqual(override_sector("멤레이비티", "기타"), "데이터센터")
        self.assertEqual(override_sector("성호전자", "IT부품·산업재"), "광통신")
        self.assertEqual(override_sector("삼성바이오로직스", "바이오"), "제약")
        self.assertEqual(override_sector("동국제약", "기타"), "제약")
        self.assertEqual(override_sector("아모레퍼시픽홀딩스", "기타"), "화장품")
        requested = {
            "DB하이텍": "반도체 전공정",
            "풍산": "방산·우주항공",
            "GS": "정유",
            "애경케미칼": "2차전지",
            "한국석유": "정유",
            "삼익제약": "제약",
            "광전자": "광통신",
            "한화엔진": "조선",
            "씨에스윈드": "에너지",
            "HD현대": "조선",
            "달바글로벌": "화장품",
            "삼양식품": "식품",
            "샘표식품": "식품",
            "삼성에스디에스": "데이터센터",
            "STX엔진": "조선",
            "크래프톤": "게임",
            "펄어비스": "게임",
            "카카오게임즈": "게임",
            "카카오게임": "게임",
            "NC": "게임",
            "컴투스": "게임",
            "삼성전기우": "기판",
        }
        for name, expected in requested.items():
            with self.subTest(name=name):
                self.assertEqual(override_sector(name, "기타"), expected)

    def test_old_sector_labels_are_merged(self):
        self.assertEqual(override_sector("미등록", "전력기기·전선"), "전력")
        self.assertEqual(override_sector("미등록", "전력기기전선"), "전력")
        self.assertEqual(override_sector("미등록", "전력/전기장비"), "전력")
        self.assertEqual(override_sector("미등록", "게임엔터테인먼트"), "게임")

    def test_duplicate_requests_keep_existing_sector(self):
        for name in ("대덕전자", "심텍", "후성", "솔브레인", "엠케이전자"):
            self.assertNotIn(name, NAME_SECTOR_OVERRIDES)
            self.assertEqual(override_sector(name, "기존 분류"), "기존 분류")

    def test_no_name_is_assigned_to_two_sectors(self):
        self.assertEqual(len(SECTOR_STOCKS), 25)
        all_names = [name.strip() for names in SECTOR_STOCKS.values() for name in names.split(",")]
        self.assertEqual(len(all_names), len(set(all_names)))

    def test_uncertain_names_are_not_guessed(self):
        self.assertNotIn("동국제강", NAME_SECTOR_OVERRIDES)
        self.assertNotIn("삼성바이오", NAME_SECTOR_OVERRIDES)


if __name__ == "__main__":
    unittest.main()
