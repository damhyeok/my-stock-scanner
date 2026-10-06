import pandas as pd
from etf_sector_flow import build_daily_return_trend, ETF_UNIVERSE


def example():
    ranking = pd.DataFrame([dict(ticker=str(i), sector=f'sector{i}', change_rate=i+1)
                            for i in range(9)])
    daily = pd.DataFrame([dict(trade_date=d, ticker=str(i), close=p)
                          for i in range(9) for d, p in [('20261001', 100), ('20261002', 110), ('20261006', 99)]])
    return daily, ranking


def test_daily_not_cumulative_and_selected_slot_not_future_close():
    daily, ranking = example()
    result = build_daily_return_trend(daily, ranking, '20261007')
    selected = result[result.ticker == '8'].set_index('date')
    assert abs(selected.loc['20261002', 'daily_return'] - 10) < 1e-8
    assert abs(selected.loc['20261006', 'daily_return'] + 10) < 1e-8
    assert selected.loc['20261007', 'daily_return'] == 9
    daily.loc[len(daily)] = ['20261007', '8', 9999]
    assert build_daily_return_trend(daily, ranking, '20261007').equals(result)


def test_only_current_top_eight_and_missing_baseline_not_zero():
    daily, ranking = example()
    result = build_daily_return_trend(daily, ranking, '20261007')
    assert result.ticker.nunique() == 8
    assert '0' not in result.ticker.tolist()
    assert result.loc[result.date == '20261001', 'daily_return'].isna().all()
    daily = daily[~((daily.ticker == '8') & (daily.trade_date == '20261002'))]
    result = build_daily_return_trend(daily, ranking, '20261007')
    assert result.loc[(result.ticker == '8') & (result.date == '20261006'), 'daily_return'].isna().all()


def test_ten_dates_and_current_without_history():
    daily, ranking = example()
    daily = pd.DataFrame([dict(trade_date=d, ticker='8', close=100)
                          for d in pd.bdate_range(end='2026-10-06', periods=20).strftime('%Y%m%d')])
    assert build_daily_return_trend(daily, ranking, '20261007').date.nunique() == 10
    result = build_daily_return_trend(pd.DataFrame(), ranking, '20261007')
    assert len(result) == 8
    assert result.daily_return.notna().all()


def test_ui_no_selection_widgets_and_all_sectors_collapsed(monkeypatch):
    import etf_sector_ui as ui
    from streamlit.testing.v1 import AppTest
    snapshots = pd.DataFrame([dict(trade_date='20261007', session='정규장(16:00)', sector=s, ticker=t, name=n,
                                  price=100, change_rate=i, trading_value=1e8, kospi_rate=0,
                                  collected_at_kst='2026-10-07 16:00:00')
                             for i, (s,t,n,note) in enumerate(ETF_UNIVERSE[:9])])
    frames = dict(etf_sector_snapshots=snapshots, etf_sector_daily=pd.DataFrame(),
                  etf_sector_holdings=pd.DataFrame(), etf_sector_runs=pd.DataFrame())
    monkeypatch.setattr(ui, 'read_etf_data', lambda *args: frames)
    app = AppTest.from_string("from etf_sector_ui import render_etf_sector_tab\nrender_etf_sector_tab('unused','v','20261007','정규장(16:00)')").run()
    assert not app.exception
    assert len(app.selectbox) == 0 and len(app.multiselect) == 0
    labels = [e.label for e in app.expander]
    assert all(s in labels for s,t,n,note in ETF_UNIVERSE[:9])
    assert len(app.dataframe[1].value.columns) == 9  # date plus eight ETFs
