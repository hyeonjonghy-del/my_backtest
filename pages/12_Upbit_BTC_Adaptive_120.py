"""Streamlit multipage entrypoint for the Upbit BTC Adaptive 120 backtest.

The page script must be executed on every Streamlit rerun.  Importing the app
module directly leaves it cached in ``sys.modules`` and can produce an empty
page for later sessions.
"""

from pathlib import Path
import runpy
import sys


APP_DIR = Path(__file__).resolve().parents[1] / "strategies" / "upbit_btc_daily"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

runpy.run_path(str(APP_DIR / "streamlit_app.py"), run_name="__main__")
