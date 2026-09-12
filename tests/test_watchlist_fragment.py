import ast
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class WatchlistFragmentTests(unittest.TestCase):
    def test_add_and_remove_refresh_the_list(self):
        tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
        functions = {node.name: ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        source = '''
import streamlit as st
import pandas as pd
st.session_state.setdefault("items", [])
def get_stock_catalog():
    return pd.DataFrame([dict(ticker="005930", name="삼성전자", market_cap=100)])
def oracle_request(method, path, json_body=None):
    if method == "POST":
        if json_body["action"] == "add":
            st.session_state["items"] = [dict(ticker="005930", name="삼성전자", added_date="20260911", entry_price=None, current_date=None, current_price=None, daily_return=None, total_return=None)]
        else:
            st.session_state["items"] = []
        return {"state":"saved", "message":"저장했습니다."}, ""
    return {"items":st.session_state["items"]}, ""
def get_watchlist_performance():
    return pd.DataFrame(st.session_state["items"])
get_watchlist_performance.clear = lambda: None
'''
        source += functions["update_oracle_watchlist"] + "\n" + functions["render_watchlist"] + "\nrender_watchlist()\n"
        at = AppTest.from_string(source).run()
        at.selectbox[0].select(("005930", "삼성전자", 100)).run()
        at.button[0].click().run()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertEqual(len(at.session_state["items"]), 1)
        self.assertEqual(len(at.dataframe[0].value), 1)
        at.selectbox[1].select(("005930", "삼성전자")).run()
        at.button[1].click().run()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertEqual(at.session_state["items"], [])
