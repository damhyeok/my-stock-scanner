import unittest
from deploy.export_web import include


class ExportTests(unittest.TestCase):
    def test_runtime_files(self):
        for path in ['app.py', 'sector_overrides.py', 'streamlit_layout.py', 'market_betting_engine/streamlit_tab.py',
                     'close_bet_staged/rule_model_ui.py', 'config/market_betting_engine.placeholder.json',
                     'web_data.bootstrap.db.gz']:
            self.assertTrue(include(path), path)

    def test_no_secrets_databases_history_or_caches(self):
        for path in ['.env', '.git/config', '.streamlit/secrets.toml', 'venv/main.py',
                     'stock_data.db', 'web_data.db.gz', 'tests/test_main.py', 'test_kis.py',
                     '.kis_token_cache.json', '.github/workflows/main.yml', 'reports/sample.csv',
                     '__pycache__/app.pyc']:
            self.assertFalse(include(path), path)
