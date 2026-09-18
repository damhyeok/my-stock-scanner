"""News-only responsive tables without an inner vertical scroll surface."""
import html
import math

import pandas as pd


def rank_news(frame):
    result = frame.copy()
    result['검토 우선순위'] = pd.to_numeric(result['importance'], errors='coerce').fillna(0) + pd.to_numeric(result['spread_bonus'], errors='coerce').fillna(0)
    return result.sort_values(['검토 우선순위', 'changed_at', 'event_id'],
                              ascending=[False, False, True], kind='stable')


def news_table_html(frame):
    def display(value):
        if pd.isna(value):
            return '—'
        if isinstance(value, float):
            return f'{value:,.2f}'.rstrip('0').rstrip('.')
        return str(value)
    headers = ''.join('<th scope="col">'+html.escape(str(c))+'</th>' for c in frame.columns)
    rows = []
    for _, row in frame.iterrows():
        cells = ''.join('<td data-label="'+html.escape(str(c), quote=True)+'">'+
                        html.escape(display(row[c]))+'</td>' for c in frame.columns)
        rows.append('<tr>'+cells+'</tr>')
    return '''<style>
.news-scroll-safe{width:100%;overflow-x:auto}
.news-scroll-safe table{width:100%;border-collapse:collapse;font-size:14px}
.news-scroll-safe th,.news-scroll-safe td{padding:9px;border-bottom:1px solid #8885;text-align:left;vertical-align:top;min-width:90px;max-width:340px;overflow-wrap:anywhere}
@media(max-width:640px){
 .news-scroll-safe{overflow:visible}
 .news-scroll-safe table,.news-scroll-safe tbody{display:block;width:100%}
 .news-scroll-safe thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
 .news-scroll-safe tr{display:block;border:1px solid #8885;border-radius:8px;margin:0 0 12px;padding:8px}
 .news-scroll-safe td{display:grid;grid-template-columns:minmax(90px,34%) minmax(0,1fr);gap:8px;min-width:0;max-width:none;padding:6px;border:0;white-space:normal}
 .news-scroll-safe td::before{content:attr(data-label);font-weight:600;opacity:.75}
}
</style><div class="news-scroll-safe"><table><thead><tr>'''+headers+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'


def render_news_table(st, frame, key, page_size=10):
    if frame.empty:
        st.caption('해당하는 이슈가 없습니다.')
        return
    pages = math.ceil(len(frame)/page_size)
    page = st.selectbox('페이지', list(range(pages)), format_func=lambda i:f'{i+1} / {pages}', key=key) if pages > 1 else 0
    st.caption(f'전체 {len(frame)}건 · {page*page_size+1}~{min((page+1)*page_size,len(frame))}번째 · 높은 점수순')
    st.markdown(news_table_html(frame.iloc[page*page_size:(page+1)*page_size]), unsafe_allow_html=True)
