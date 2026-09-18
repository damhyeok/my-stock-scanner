import unittest
from unittest.mock import Mock
import pandas as pd
from news_ui import rank_news, news_table_html, render_news_table


class NewsUiTests(unittest.TestCase):
    def test_score_descending_then_recent_change(self):
        frame = pd.DataFrame([
            dict(event_id='low',importance=1,spread_bonus=.5,changed_at='2026-09-17 15:00'),
            dict(event_id='old',importance=3,spread_bonus=.2,changed_at='2026-09-17 09:00'),
            dict(event_id='new',importance=3,spread_bonus=.2,changed_at='2026-09-17 14:00'),
        ])
        ranked = rank_news(frame)
        self.assertEqual(ranked.event_id.tolist(), ['new','old','low'])
        self.assertEqual(ranked['검토 우선순위'].tolist(), [3.2,3.2,1.5])

    def test_html_escapes_news_and_wraps_mobile_labels(self):
        result = news_table_html(pd.DataFrame([{'핵심 이슈':'<script>alert(1)</script>','수익률':None}]))
        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn('data-label="핵심 이슈"', result)
        self.assertIn('@media(max-width:640px)', result)
        self.assertIn('—', result)
        self.assertNotIn('overflow-y:auto', result)

    def test_pagination_renders_only_selected_ten(self):
        st = Mock()
        st.selectbox.return_value = 1
        render_news_table(st,pd.DataFrame({'종목':[f'stock-{i:02}' for i in range(25)]}),'test')
        rendered = st.markdown.call_args.args[0]
        self.assertIn('stock-10',rendered)
        self.assertIn('stock-19',rendered)
        self.assertNotIn('stock-09',rendered)
        self.assertNotIn('stock-20',rendered)
