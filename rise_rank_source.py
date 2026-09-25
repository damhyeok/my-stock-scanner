"""Bounded range queries for a ranking endpoint capped at 30 rows."""
def collect_rise_rows(fetch, eligible, target=60, max_calls=40):
    found = {}
    calls = 0

    def visit(low, high, price_low=0, price_high=10000000):
        nonlocal calls
        if len(eligible(list(found.values()))) >= target:
            return
        if calls >= max_calls:
            raise RuntimeError('상승률 구간 조회 상한 도달: 불완전한 TOP60은 저장하지 않습니다.')
        calls += 1
        rows = fetch(low / 100, high / 100, price_low, price_high)
        for row in rows:
            ticker = str(row.get('stck_shrn_iscd') or row.get('mksc_shrn_iscd') or '').strip()
            if ticker:
                found[ticker] = row
        if len(rows) < 30 or len(eligible(list(found.values()))) >= target:
            return
        # Search higher-return intervals first. Cents match the API rate precision.
        if low < high:
            mid = (low + high) // 2
            visit(mid + 1, high, price_low, price_high)
            visit(low, mid, price_low, price_high)
        elif price_low < price_high:
            # More than 30 names can share one rounded return (e.g. limit-up).
            mid = (price_low + price_high) // 2
            visit(low, high, mid + 1, price_high)
            visit(low, high, price_low, mid)
        else:
            raise RuntimeError('동일 상승률·가격의 종목이 응답 상한에 도달해 순위 범위를 확인할 수 없습니다.')

    visit(0, 3000)
    return list(found.values())
