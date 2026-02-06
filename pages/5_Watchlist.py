"""
Watchlist - Track markets, set price alerts, mini sparklines.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from utils import (
    cached_fetch_market,
    cached_search_markets,
    cached_fetch_market_history,
    make_watchlist_entry,
    parse_market,
    fmt_pct,
    fmt_usd,
)

st.header("Watchlist")

# ==========================================================================
# Add Market
# ==========================================================================

with st.expander("Add Market to Watchlist"):
    wl_query = st.text_input("Search market", key="wl_search")

    if wl_query:
        results = cached_search_markets(wl_query, limit=5)
        if results:
            options = {
                f"{r.get('question', r.get('title', '?'))[:70]} ({r.get('conditionId', '')[:8]})": r
                for r in results
            }
            selected = st.selectbox("Select market", list(options.keys()), key="wl_sel")
            market_raw = options.get(selected)
        else:
            market_raw = None
            st.warning("No markets found.")

        if market_raw:
            m = parse_market(market_raw)
            alert_price = st.number_input(
                "Alert when YES price crosses",
                0.01, 0.99, 0.50, 0.01,
                key="wl_alert",
            )

            if st.button("Add to Watchlist", key="wl_add"):
                entry = make_watchlist_entry(m, alert_price=alert_price)
                st.session_state.watchlist.append(entry)
                st.toast(f"Added: {m['question'][:50]}")
                st.rerun()

# ==========================================================================
# Display Watchlist
# ==========================================================================

watchlist = st.session_state.get("watchlist", [])

if not watchlist:
    st.info("Watchlist is empty. Add markets above or from the Scanner page.")
    st.stop()

for i, entry in enumerate(watchlist):
    question = entry.get("question", "Unknown")
    condition_id = entry.get("condition_id", "")
    added_price = entry.get("added_price")
    alert_price = entry.get("alert_price")
    entry_id = entry.get("id", str(i))

    # Fetch live price (cached 60s)
    current_price = added_price
    try:
        if condition_id:
            market = cached_fetch_market(condition_id)
            pm = parse_market(market)
            current_price = pm["yes_price"] or current_price
    except Exception:
        pass

    # Change calculation
    change_pp = None
    if current_price is not None and added_price is not None:
        change_pp = (current_price - added_price) * 100

    # Alert check
    alert_triggered = False
    if alert_price and current_price:
        if added_price and added_price < alert_price and current_price >= alert_price:
            alert_triggered = True
        elif added_price and added_price > alert_price and current_price <= alert_price:
            alert_triggered = True

    if alert_triggered:
        st.warning(
            f"ALERT: {question[:60]} crossed {fmt_pct(alert_price)}! "
            f"Now at {fmt_pct(current_price)}"
        )

    # Row layout
    col1, col2, col3, col4, col5, col6 = st.columns([4, 1, 1, 1, 1, 1])

    col1.write(f"**{question[:60]}**")
    col2.write(f"Added: {fmt_pct(added_price)}")
    col3.write(f"Now: {fmt_pct(current_price)}")

    if change_pp is not None:
        color = "green" if change_pp > 0 else "red" if change_pp < 0 else "gray"
        col4.write(f":{color}[{change_pp:+.1f}pp]")
    else:
        col4.write("N/A")

    col5.write(f"Alert: {fmt_pct(alert_price)}")

    # Use stable ID for button key
    if col6.button("Remove", key=f"wl_rm_{entry_id}"):
        st.session_state.watchlist.pop(i)
        st.rerun()

    # Mini sparkline (7d)
    token_ids = entry.get("clob_token_ids", [])
    if token_ids:
        try:
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            end = now.strftime("%Y-%m-%d")

            df = cached_fetch_market_history(
                condition_id or entry.get("slug", ""),
                start_date=start,
                end_date=end,
                fidelity=360,  # 6hr candles for 7d
            )

            if not df.is_empty():
                yes_col = None
                for col in df.columns:
                    if "yes" in col.lower():
                        yes_col = col
                        break

                if yes_col:
                    fig = go.Figure()
                    fig.add_trace(
                        go.Scatter(
                            x=df["timestamp"].to_list(),
                            y=df[yes_col].to_list(),
                            mode="lines",
                            line=dict(color="#3b82f6", width=1.5),
                            showlegend=False,
                        )
                    )
                    fig.update_layout(
                        height=60,
                        margin=dict(l=0, r=0, t=0, b=0),
                        xaxis=dict(visible=False),
                        yaxis=dict(visible=False, range=[0, 1]),
                    )
                    st.plotly_chart(fig, use_container_width=True, key=f"spark_{entry_id}")
        except Exception:
            pass

    st.divider()

# ==========================================================================
# Export / Import
# ==========================================================================

st.subheader("Watchlist Export / Import")

col_ex, col_im = st.columns(2)

with col_ex:
    wl_json = json.dumps(st.session_state.watchlist, indent=2, default=str)
    st.download_button(
        "Export Watchlist",
        data=wl_json,
        file_name="polymarket_watchlist.json",
        mime="application/json",
    )

with col_im:
    uploaded = st.file_uploader("Import Watchlist", type=["json"], key="wl_import")
    if uploaded:
        try:
            loaded = json.loads(uploaded.read())
            if isinstance(loaded, list):
                # Ensure all entries have IDs
                for entry in loaded:
                    if "id" not in entry:
                        entry["id"] = str(uuid.uuid4())
                st.session_state.watchlist = loaded
                st.success(f"Imported {len(loaded)} markets!")
                st.rerun()
            else:
                st.error("Expected a JSON array.")
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
