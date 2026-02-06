"""
Bankroll Management - Kelly calculator, position tracker, growth simulator.
"""

import json
import math
import random
import uuid
from datetime import datetime, timezone

import plotly.graph_objects as go
import streamlit as st

from utils import (
    cached_fetch_market,
    cached_search_markets,
    parse_market,
    kelly_criterion,
    half_kelly,
    quarter_kelly,
    expected_value,
    fmt_pct,
    fmt_usd,
)

st.header("Bankroll Management")

# ==========================================================================
# Kelly Calculator
# ==========================================================================

st.subheader("Kelly Criterion Calculator")

col1, col2 = st.columns(2)

with col1:
    market_price = st.slider(
        "Market price (current probability)",
        0.01, 0.99, 0.30, 0.01,
        key="kelly_price",
    )
    true_prob = st.slider(
        "Your estimated true probability",
        0.01, 0.99, 0.50, 0.01,
        key="kelly_true",
    )
    kelly_mode = st.radio(
        "Kelly mode",
        ["Full Kelly", "Half Kelly", "Quarter Kelly"],
        index=1,
        key="kelly_mode",
    )

with col2:
    if kelly_mode == "Full Kelly":
        fraction = kelly_criterion(true_prob, market_price)
    elif kelly_mode == "Half Kelly":
        fraction = half_kelly(true_prob, market_price)
    else:
        fraction = quarter_kelly(true_prob, market_price)

    ev = expected_value(true_prob, market_price)
    payout = 1.0 / market_price if market_price > 0 else 0
    bet_size = fraction * st.session_state.bankroll

    st.metric("Kelly Fraction", f"{fraction:.1%}")
    st.metric("Recommended Bet", fmt_usd(bet_size))
    st.metric("Expected Value", f"{ev:+.1%}")
    st.metric("Payout Multiplier", f"{payout:.2f}x")

    if ev > 0:
        st.success(f"Positive edge: {ev:+.1%} EV")
    elif ev < 0:
        st.error(f"Negative edge: {ev:+.1%} EV - no bet!")
    else:
        st.info("Break even")

# EV Curve
st.caption("Expected growth rate vs bet fraction")

fractions_range = [i / 100.0 for i in range(0, 101)]
growth_rates = []
for f in fractions_range:
    if 0 < market_price < 1:
        b = (1.0 / market_price) - 1.0
        p = true_prob
        q = 1.0 - p
        if f < 1.0 and (1.0 + f * b) > 0:
            g = p * math.log(1 + f * b) + q * math.log(max(1 - f, 1e-10))
        else:
            g = -10
        growth_rates.append(g)
    else:
        growth_rates.append(0)

fig_ev = go.Figure()
fig_ev.add_trace(
    go.Scatter(
        x=fractions_range,
        y=growth_rates,
        mode="lines",
        name="Growth Rate",
        line=dict(color="#3b82f6", width=2),
    )
)
fig_ev.add_vline(
    x=kelly_criterion(true_prob, market_price),
    line_dash="dash",
    line_color="#22c55e",
    annotation_text="Full Kelly",
)
fig_ev.add_vline(
    x=fraction,
    line_dash="dot",
    line_color="#f59e0b",
    annotation_text=kelly_mode,
)
fig_ev.update_layout(
    height=300,
    xaxis=dict(title="Bet Fraction", tickformat=".0%"),
    yaxis=dict(title="Expected Log Growth"),
    margin=dict(l=40, r=20, t=20, b=40),
)
st.plotly_chart(fig_ev, use_container_width=True)

# ==========================================================================
# Position Tracker
# ==========================================================================

st.subheader("Position Tracker")

with st.expander("Add Position"):
    pos_query = st.text_input("Search market", key="pos_search")
    pos_market = None

    if pos_query:
        results = cached_search_markets(pos_query, limit=5)
        if results:
            options = {
                f"{r.get('question', r.get('title', '?'))[:70]} ({r.get('conditionId', '')[:8]})": r
                for r in results
            }
            sel = st.selectbox("Market", list(options.keys()), key="pos_sel")
            pos_market = options.get(sel)

    pos_side = st.radio("Side", ["YES", "NO"], key="pos_side", horizontal=True)
    pos_price = st.number_input(
        "Entry price", 0.01, 0.99, 0.50, 0.01, key="pos_price"
    )
    pos_size = st.number_input(
        "Position size ($)", 0.0, 10000.0, 10.0, 1.0, key="pos_size"
    )

    if st.button("Add Position", key="pos_add") and pos_market:
        if pos_size > st.session_state.bankroll:
            st.error(
                f"Insufficient bankroll (${st.session_state.bankroll:,.2f}). "
                f"Reduce position size."
            )
        else:
            m = parse_market(pos_market)
            position = {
                "id": str(uuid.uuid4()),
                "question": m["question"],
                "condition_id": m["condition_id"],
                "side": pos_side,
                "entry_price": pos_price,
                "size": pos_size,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            st.session_state.positions.append(position)
            st.session_state.bankroll -= pos_size
            st.toast(f"Position added: {pos_side} @ {pos_price:.2f}")
            st.rerun()

# Display positions
if st.session_state.positions:
    display_positions = []
    position_values: dict[str, float] = {}  # id -> current mark-to-market value

    for pos in st.session_state.positions:
        current_price = pos["entry_price"]  # fallback

        # Try to get live price (cached 60s)
        try:
            if pos["condition_id"]:
                market = cached_fetch_market(pos["condition_id"])
                pm = parse_market(market)
                if pos["side"] == "YES":
                    current_price = pm["yes_price"] or current_price
                else:
                    current_price = pm["no_price"] or current_price
        except Exception:
            pass

        # P&L: shares = size / entry_price, value = shares * current_price
        # P&L = value - cost = size * (current_price / entry_price - 1)
        entry = pos["entry_price"]
        if entry > 0:
            current_value = pos["size"] * (current_price / entry)
            pnl = current_value - pos["size"]
        else:
            current_value = pos["size"]
            pnl = 0.0

        pnl_pct = (pnl / pos["size"] * 100) if pos["size"] > 0 else 0
        position_values[pos["id"]] = current_value

        display_positions.append(
            {
                "Question": pos["question"][:60],
                "Side": pos["side"],
                "Entry": f"{pos['entry_price']:.2f}",
                "Current": f"{current_price:.2f}",
                "Size": fmt_usd(pos["size"]),
                "Value": fmt_usd(current_value),
                "P&L": f"${pnl:+,.2f}",
                "P&L %": f"{pnl_pct:+.1f}%",
            }
        )

    st.dataframe(display_positions, use_container_width=True)

    # Close position buttons - use stable IDs
    st.caption("Close a position:")
    cols = st.columns(min(len(st.session_state.positions), 5))
    for i, pos in enumerate(st.session_state.positions):
        pos_id = pos.get("id", str(i))
        col_idx = i % len(cols)
        if cols[col_idx].button(
            f"Close #{i + 1}", key=f"close_{pos_id}"
        ):
            # Return mark-to-market value, not cost basis
            current_value = position_values.get(pos_id, pos["size"])
            closed = st.session_state.positions.pop(i)
            closed["closed_at"] = datetime.now(timezone.utc).isoformat()
            closed["close_value"] = current_value
            st.session_state.trade_log.append(closed)
            st.session_state.bankroll += current_value
            st.toast(f"Position closed for {fmt_usd(current_value)}")
            st.rerun()
else:
    st.info("No open positions.")

# ==========================================================================
# Growth Simulator
# ==========================================================================

st.subheader("Growth Simulator (Monte Carlo)")

sim_col1, sim_col2 = st.columns(2)

with sim_col1:
    sim_bankroll = st.number_input(
        "Starting bankroll ($)", 10.0, 100_000.0,
        st.session_state.bankroll, 10.0,
        key="sim_bankroll",
    )
    sim_edge = st.slider("Avg edge (%)", 1, 30, 5, key="sim_edge")
    sim_bets = st.slider("Bets per day", 1, 20, 3, key="sim_bets")

with sim_col2:
    sim_kelly_frac = st.slider(
        "Kelly fraction", 0.05, 1.0, 0.25, 0.05, key="sim_kelly"
    )
    sim_days = st.slider("Days", 10, 365, 90, key="sim_days")
    sim_paths = 100

if st.button("Run Simulation", key="sim_btn"):
    all_paths = []
    final_values = []
    rng = random.Random(42)  # reproducible seed

    edge = sim_edge / 100.0
    avg_price = 0.5
    avg_true_prob = 0.5 + edge
    kelly_f = kelly_criterion(avg_true_prob, avg_price) * sim_kelly_frac

    for _ in range(sim_paths):
        bankroll_path = [sim_bankroll]
        b = sim_bankroll

        for day in range(sim_days):
            for _ in range(sim_bets):
                bet = b * kelly_f
                if rng.random() < avg_true_prob:
                    b += bet * ((1.0 / avg_price) - 1.0)
                else:
                    b -= bet
                b = max(b, 0.01)
            bankroll_path.append(b)

        all_paths.append(bankroll_path)
        final_values.append(b)

    # Compute percentiles
    days_axis = list(range(sim_days + 1))
    median = []
    p10 = []
    p90 = []

    for d in range(sim_days + 1):
        vals = sorted([path[d] for path in all_paths])
        median.append(vals[len(vals) // 2])
        p10.append(vals[len(vals) // 10])
        p90.append(vals[int(len(vals) * 0.9)])

    fig_sim = go.Figure()

    fig_sim.add_trace(
        go.Scatter(
            x=days_axis, y=p90,
            mode="lines", name="90th pct",
            line=dict(color="#22c55e", width=1, dash="dot"),
        )
    )
    fig_sim.add_trace(
        go.Scatter(
            x=days_axis, y=p10,
            mode="lines", name="10th pct",
            line=dict(color="#ef4444", width=1, dash="dot"),
            fill="tonexty",
            fillcolor="rgba(59, 130, 246, 0.1)",
        )
    )
    fig_sim.add_trace(
        go.Scatter(
            x=days_axis, y=median,
            mode="lines", name="Median",
            line=dict(color="#3b82f6", width=3),
        )
    )

    fig_sim.update_layout(
        height=400,
        xaxis_title="Day",
        yaxis_title="Bankroll ($)",
        margin=dict(l=40, r=20, t=20, b=40),
    )
    st.plotly_chart(fig_sim, use_container_width=True)

    # Summary stats
    c1, c2, c3 = st.columns(3)
    c1.metric("Median Final", fmt_usd(median[-1]))
    c2.metric("10th Percentile", fmt_usd(p10[-1]))
    c3.metric("90th Percentile", fmt_usd(p90[-1]))
