"""
Polymarket OSINT Edge Dashboard - Entry Point.

Run: streamlit run app.py
"""

import json

import streamlit as st

from utils import get_provider, fmt_usd

st.set_page_config(
    layout="wide",
    page_title="Polymarket Edge Finder",
    page_icon="\U0001F4C8",
)

# ==========================================================================
# Session State Init
# ==========================================================================

if "watchlist" not in st.session_state:
    st.session_state.watchlist = []  # list of parsed market dicts

if "positions" not in st.session_state:
    st.session_state.positions = []  # list of position dicts

if "trade_log" not in st.session_state:
    st.session_state.trade_log = []

if "bankroll" not in st.session_state:
    st.session_state.bankroll = 100.0

if "selected_market" not in st.session_state:
    st.session_state.selected_market = None

# ==========================================================================
# Sidebar
# ==========================================================================

with st.sidebar:
    st.title("Polymarket Edge Finder")

    # API status
    provider = get_provider()
    connected = provider.validate_connection()
    if connected:
        st.success("API Connected", icon="\u2705")
    else:
        st.error("API Disconnected", icon="\u274c")

    st.divider()

    # Bankroll
    st.metric("Bankroll", fmt_usd(st.session_state.bankroll))
    new_bankroll = st.number_input(
        "Set bankroll ($)",
        min_value=0.0,
        value=st.session_state.bankroll,
        step=10.0,
        key="bankroll_input",
    )
    if new_bankroll != st.session_state.bankroll:
        st.session_state.bankroll = new_bankroll

    st.divider()

    # Save / Load State
    st.subheader("Save / Load State")

    state_data = json.dumps(
        {
            "bankroll": st.session_state.bankroll,
            "watchlist": st.session_state.watchlist,
            "positions": st.session_state.positions,
            "trade_log": st.session_state.trade_log,
        },
        indent=2,
        default=str,
    )
    st.download_button(
        "Export State (JSON)",
        data=state_data,
        file_name="polymarket_state.json",
        mime="application/json",
    )

    uploaded = st.file_uploader("Import State", type=["json"])
    if uploaded is not None and not st.session_state.get("_import_done"):
        try:
            loaded = json.loads(uploaded.read())
            # Basic validation
            bankroll = float(loaded.get("bankroll", 100.0))
            if not (0 <= bankroll <= 1_000_000):
                bankroll = 100.0
            st.session_state.bankroll = bankroll
            wl = loaded.get("watchlist", [])
            st.session_state.watchlist = wl if isinstance(wl, list) else []
            pos = loaded.get("positions", [])
            st.session_state.positions = pos if isinstance(pos, list) else []
            tl = loaded.get("trade_log", [])
            st.session_state.trade_log = tl if isinstance(tl, list) else []
            st.session_state._import_done = True
            st.success("State loaded!")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            st.error(f"Invalid state file: {e}")

# ==========================================================================
# Home Page
# ==========================================================================

st.header("Polymarket OSINT Edge Dashboard")

st.markdown("""
**Strategy**: Find mispricings in prediction markets using public data,
size bets with Kelly criterion, and compound capital through quick-resolving markets.

### Pages

| Page | Purpose |
|------|---------|
| **Scanner** | Scan for mispricings, asymmetric bets, longshots, close races |
| **Deep Dive** | Charts, orderbook, trades for a single market |
| **OSINT Edge** | News cross-reference, volume anomalies, resolution timeline, smart money |
| **Bankroll** | Kelly calculator, position tracker, growth simulator |
| **Watchlist** | Track markets, set price alerts |
""")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Open Positions", len(st.session_state.positions))

with col2:
    st.metric("Watchlist", len(st.session_state.watchlist))

with col3:
    st.metric("Total Trades", len(st.session_state.trade_log))
