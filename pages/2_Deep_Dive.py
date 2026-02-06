"""
Deep Dive - Single market analysis with charts, orderbook, trades.
"""

from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from utils import (
    cached_fetch_market_history,
    cached_fetch_orderbook,
    cached_fetch_trades,
    cached_search_markets,
    get_provider,
    parse_market,
    fmt_pct,
    fmt_usd,
    days_until,
    polymarket_url,
)

st.header("Deep Dive")

# ==========================================================================
# Market Selector
# ==========================================================================

search_query = st.text_input("Search markets", placeholder="e.g. Bitcoin, Trump, Fed")

market_data = None

if search_query:
    results = cached_search_markets(search_query, limit=10)
    if results:
        options = {
            m.get("question", m.get("title", "?"))[:80]: m for m in results
        }
        selected_q = st.selectbox("Select market", list(options.keys()))
        if selected_q:
            market_data = options[selected_q]
    else:
        st.warning("No markets found.")

# Check session state for market passed from Scanner
if market_data is None and st.session_state.get("selected_market"):
    market_data = st.session_state.selected_market
    st.session_state.selected_market = None

if market_data is None:
    st.info("Search for a market or navigate from Scanner.")
    st.stop()

m = parse_market(market_data)

# ==========================================================================
# Row 1: Key Metrics
# ==========================================================================

st.subheader(m["question"])

if m["event_slug"]:
    st.caption(f"[View on Polymarket]({polymarket_url(m['event_slug'])})")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("YES Price", fmt_pct(m["yes_price"]))
col2.metric("NO Price", fmt_pct(m["no_price"]))
col3.metric("Volume", fmt_usd(m["volume"]))
col4.metric("Liquidity", fmt_usd(m["liquidity"]))

du = days_until(m["end_date"])
col5.metric("Days to Resolution", du if du is not None else "N/A")

# ==========================================================================
# Row 2: Historical Probability Chart
# ==========================================================================

st.subheader("Historical Probability")

range_options = {"7d": 7, "30d": 30, "90d": 90, "All": 365}
range_sel = st.radio(
    "Date range", list(range_options.keys()), horizontal=True, index=1
)
days_back = range_options[range_sel]

now = datetime.now(timezone.utc)
start_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
end_date = now.strftime("%Y-%m-%d")

market_id = m["condition_id"] or m["slug"]
if not market_id:
    st.warning("No market ID available for history.")
else:
    fidelity = 60 if days_back <= 30 else 1440
    try:
        df = cached_fetch_market_history(
            market_id,
            start_date=start_date,
            end_date=end_date,
            fidelity=fidelity,
        )

        if not df.is_empty():
            fig = go.Figure()

            for col in df.columns:
                if col == "timestamp":
                    continue
                color = "#22c55e" if "yes" in col.lower() else "#ef4444"
                label = col.replace("_price", "").upper()
                fig.add_trace(
                    go.Scatter(
                        x=df["timestamp"].to_list(),
                        y=df[col].to_list(),
                        mode="lines",
                        name=label,
                        line=dict(color=color, width=2),
                    )
                )

            fig.update_layout(
                yaxis=dict(range=[0, 1], tickformat=".0%", title="Probability"),
                xaxis=dict(title="Date"),
                height=400,
                margin=dict(l=40, r=20, t=20, b=40),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No historical data available for this market.")
    except Exception as e:
        st.error(f"Failed to fetch history: {e}")

# ==========================================================================
# Row 3: Orderbook & Trades
# ==========================================================================

col_ob, col_trades = st.columns(2)

# Orderbook
with col_ob:
    st.subheader("Order Book")

    token_ids = m["clob_token_ids"]
    if token_ids:
        # Show YES token orderbook
        try:
            book = cached_fetch_orderbook(token_ids[0])
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if bids or asks:
                fig_ob = go.Figure()

                if bids:
                    bid_prices = [float(b.get("price", b[0]) if isinstance(b, dict) else b[0]) for b in bids[:20]]
                    bid_sizes = [float(b.get("size", b[1]) if isinstance(b, dict) else b[1]) for b in bids[:20]]
                    fig_ob.add_trace(
                        go.Bar(
                            y=[f"{p:.2f}" for p in bid_prices],
                            x=bid_sizes,
                            orientation="h",
                            name="Bids",
                            marker_color="#22c55e",
                        )
                    )

                if asks:
                    ask_prices = [float(a.get("price", a[0]) if isinstance(a, dict) else a[0]) for a in asks[:20]]
                    ask_sizes = [float(a.get("size", a[1]) if isinstance(a, dict) else a[1]) for a in asks[:20]]
                    fig_ob.add_trace(
                        go.Bar(
                            y=[f"{p:.2f}" for p in ask_prices],
                            x=[-s for s in ask_sizes],
                            orientation="h",
                            name="Asks",
                            marker_color="#ef4444",
                        )
                    )

                fig_ob.update_layout(
                    height=350,
                    margin=dict(l=40, r=20, t=20, b=40),
                    barmode="relative",
                    xaxis_title="Size",
                    yaxis_title="Price",
                )
                st.plotly_chart(fig_ob, use_container_width=True)
            else:
                st.info("No limit orders on the book.")
        except Exception as e:
            st.error(f"Orderbook fetch failed: {e}")
    else:
        st.info("No CLOB token IDs for this market.")

# Trades
with col_trades:
    st.subheader("Recent Trades")

    cond_id = m["condition_id"]
    if cond_id:
        try:
            trades = cached_fetch_trades(market=cond_id, limit=100)
            if trades:
                display_trades = []
                total_size = 0.0
                for t in trades[:50]:
                    side = t.get("side", "unknown")
                    price = t.get("price", "")
                    size = float(t.get("size", 0))
                    total_size += size
                    timestamp = t.get("timestamp", t.get("created_at", ""))
                    display_trades.append(
                        {
                            "Side": side.upper(),
                            "Price": f"{float(price):.2f}" if price else "",
                            "Size": f"${size:,.0f}",
                            "Time": str(timestamp)[:19],
                        }
                    )

                avg_size = total_size / len(trades) if trades else 0
                st.caption(f"Avg trade size: {fmt_usd(avg_size)}")
                st.dataframe(display_trades, use_container_width=True, height=350)
            else:
                st.info("No recent trades found.")
        except Exception as e:
            st.error(f"Trade fetch failed: {e}")
    else:
        st.info("No condition ID for trade lookup.")

# ==========================================================================
# Row 4: Related Markets
# ==========================================================================

st.subheader("Related Markets (Same Event)")

event_slug = m.get("event_slug") or market_data.get("eventSlug", "")
if event_slug:
    try:
        provider = get_provider()
        event = provider.fetch_event(event_slug)
        sibling_markets = event.get("markets", [])

        if sibling_markets:
            related = []
            for sm in sibling_markets:
                pm = parse_market(sm)
                related.append(
                    {
                        "Question": pm["question"][:80],
                        "YES": fmt_pct(pm["yes_price"]),
                        "NO": fmt_pct(pm["no_price"]),
                        "Volume": fmt_usd(pm["volume"]),
                    }
                )
            st.dataframe(related, use_container_width=True)
        else:
            st.info("No sibling markets found.")
    except Exception:
        st.info("Could not fetch parent event.")
else:
    st.info("No event slug available.")
