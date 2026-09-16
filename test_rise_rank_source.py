import unittest
from rise_rank_source import collect_rise_rows


class RiseSourceTest(unittest.TestCase):
    def test_collects_sixty_across_capped_intervals(self):
        source = [dict(stck_shrn_iscd=str(i), rate=29-i*.2, price=1000+i) for i in range(100)]
        def fetch(low, high, p1, p2):
            return [r for r in source if low <= round(r['rate'],2) <= high and p1 <= r['price'] <= p2][:30]
        rows = collect_rise_rows(fetch, lambda rows: rows)
        expected = set(str(i) for i in range(60))
        actual = {r['stck_shrn_iscd'] for r in sorted(rows,key=lambda r:r['rate'],reverse=True)[:60]}
        self.assertEqual(actual, expected)

    def test_equal_rate_uses_price_split(self):
        source = [dict(stck_shrn_iscd=str(i), rate=30, price=1000+i*10000) for i in range(80)]
        def fetch(low, high, p1, p2):
            return [r for r in source if low <= r['rate'] <= high and p1 <= r['price'] <= p2][:30]
        self.assertGreaterEqual(len(collect_rise_rows(fetch,lambda r:r)),60)

    def test_unresponsive_filters_fail_instead_of_fabricating_ranks(self):
        with self.assertRaises(RuntimeError):
            collect_rise_rows(lambda *a:[dict(stck_shrn_iscd=str(i)) for i in range(30)],lambda r:r,max_calls=5)
