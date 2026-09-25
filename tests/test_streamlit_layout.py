import unittest
from unittest.mock import patch

from streamlit_layout import layout_width


class LayoutTests(unittest.TestCase):
    def test_modern(self):
        with patch('streamlit_layout.version', return_value='1.51.0'):
            self.assertEqual(layout_width(), {'width': 'stretch'})
            self.assertEqual(layout_width(False), {'width': 'content'})

    def test_legacy_oracle(self):
        with patch('streamlit_layout.version', return_value='1.40.1'):
            self.assertEqual(layout_width(), {'use_container_width': True})
            self.assertEqual(layout_width(False), {'use_container_width': False})
