"""
Market Scanner - Find edges across Polymarket.

4 analysis sections:
1. Multi-Outcome Mispricings (Arbitrage)
2. Top EV Asymmetric Bets
3. Contrarian Longshots
4. Closest Races
"""

import streamlit as st

from utils import (
    cached_fetch_events,
    cached_fetch_tags,
    cached_search_markets,
    make_watchlist_entry,
    parse_market,
    is_non_exclusive,
    asymmetry_score,
    fmt_pct,
    fmt_usd,
    polymarket_url,
    sanitize_md,
)

st.header("Market Scanner")

# ==========================================================================
# Sidebar Filters
# ==========================================================================

with st.sidebar:
    st.subheader("Scanner Filters")

    min_volume = st.number_input(
        "Min Volume ($)", min_value=0, value=10_000, step=5_000
    )
    min_liquidity = st.number_input(
        "Min Liquidity ($)", min_value=0, value=1_000, step=1_000
    )

    # Tags
    try:
        tags = cached_fetch_tags(limit=50)
        tag_options = ["All"] + [t.get("label", t.get("slug", "")) for t in tags]
        tag_slugs = [None] + [t.get("slug", "") for t in tags]
    except Exception:
        tag_options = ["All"]
        tag_slugs = [None]

    tag_idx = st.selectbox("Category", options=range(len(tag_options)),
                           format_func=lambda i: tag_options[i])
    selected_tag = tag_slugs[tag_idx]

    search_text = st.text_input("Search Text")
    max_outcomes = st.slider("Max Outcomes per Event", 2, 50, 20)
    num_events = st.slider("Events to Scan", 50, 500, 200, step=50)

# ==========================================================================
# Data Fetching
# ==========================================================================

scan_btn = st.button("Scan Markets", type="primary", use_container_width=True)

if scan_btn:
    with st.spinner("Fetching events..."):
        all_events = []
        # Paginate through events
        for offset in range(0, num_events, 100):
            batch = cached_fetch_events(
                active=True,
                limit=100,
                offset=offset,
                tag_slug=selected_tag,
                order="volume",
                ascending=False,
            )
            if not batch:
                break
            all_events.extend(batch)

    st.session_state["scan_events"] = all_events
    st.session_state["scan_filters"] = {
        "min_volume": min_volume,
        "min_liquidity": min_liquidity,
        "search_text": search_text,
        "max_outcomes": max_outcomes,
    }

# Use cached scan results
events = st.session_state.get("scan_events", [])
filters = st.session_state.get("scan_filters", {})

if not events:
    st.info("Click 'Scan Markets' to begin.")
    st.stop()

filt_min_vol = filters.get("min_volume", min_volume)
filt_min_liq = filters.get("min_liquidity", min_liquidity)
filt_search = filters.get("search_text", search_text)
filt_max_outcomes = filters.get("max_outcomes", max_outcomes)

# Parse all markets from events
all_markets = []
event_market_map: dict = {}  # event_slug -> list of parsed markets

for event in events:
    event_title = event.get("title", "Untitled")
    event_slug = event.get("slug", "")
    markets_raw = event.get("markets", [])

    parsed = []
    for m in markets_raw:
        pm = parse_market(m)
        pm["event_title"] = event_title
        pm["event_slug"] = event_slug

        # Apply text filter
        if filt_search and filt_search.lower() not in pm["question"].lower():
            continue

        parsed.append(pm)
        all_markets.append(pm)

    if parsed:
        event_market_map[event_slug] = {
            "title": event_title,
            "markets": parsed,
        }

st.caption(f"Scanned {len(events)} events, {len(all_markets)} markets")

# ==========================================================================
# Section 1: Multi-Outcome Mispricings
# ==========================================================================

st.subheader("1. Multi-Outcome Mispricings (Arbitrage)")
st.caption(
    "Events with 3+ outcomes where probabilities don't sum to 1.0. "
    "Excludes cumulative/threshold/sports prop markets."
)

mispricing_rows = []
for slug, info in event_market_map.items():
    markets = info["markets"]

    # Filter: need 3+ outcomes, not too many
    if len(markets) < 3 or len(markets) > filt_max_outcomes:
        continue

    # Check if any market looks non-exclusive
    has_non_exclusive = any(is_non_exclusive(m["question"]) for m in markets)
    if has_non_exclusive:
        continue

    # Sum YES prices
    prices = [m["yes_price"] for m in markets if m["yes_price"] is not None]
    if not prices:
        continue

    total_volume = sum(m["volume"] for m in markets)
    if total_volume < filt_min_vol:
        continue

    prob_sum = sum(prices)
    deviation = abs(prob_sum - 1.0)

    if deviation > 0.03:
        direction = "Buy all (sum < 1)" if prob_sum < 1.0 else "Fade weakest (sum > 1)"
        mispricing_rows.append(
            {
                "Event": info["title"],
                "Outcomes": len(markets),
                "P(sum)": f"{prob_sum:.3f}",
                "Deviation": f"{deviation:.3f}",
                "Direction": direction,
                "Volume": fmt_usd(total_volume),
                "slug": slug,
                "markets": markets,
            }
        )

mispricing_rows.sort(key=lambda r: float(r["Deviation"]), reverse=True)

if mispricing_rows:
    for row in mispricing_rows[:20]:
        with st.expander(
            f"{row['Event']} | P(sum)={row['P(sum)']} | {row['Direction']}"
        ):
            cols = st.columns([3, 1, 1, 1])
            cols[0].write(f"**{row['Outcomes']}** outcomes | {row['Volume']} volume")
            cols[1].metric("P(sum)", row["P(sum)"])
            cols[2].metric("Deviation", row["Deviation"])

            # Outcome table
            outcome_data = []
            for m in row["markets"]:
                outcome_data.append(
                    {
                        "Question": m["question"],
                        "YES Price": fmt_pct(m["yes_price"]),
                        "Volume": fmt_usd(m["volume"]),
                    }
                )
            st.dataframe(outcome_data, use_container_width=True)

            c1, c2 = st.columns(2)
            if c1.button("Deep Dive", key=f"dd_{row['slug']}"):
                if row["markets"]:
                    st.session_state.selected_market = row["markets"][0]["raw"]
                    st.switch_page("pages/2_Deep_Dive.py")
            if c2.button("Add to Watchlist", key=f"wl_{row['slug']}"):
                for m in row["markets"][:3]:
                    st.session_state.watchlist.append(make_watchlist_entry(m))
                st.toast(f"Added {min(3, len(row['markets']))} markets to watchlist")
else:
    st.info("No multi-outcome mispricings found with current filters.")

# ==========================================================================
# Section 2: Top EV Asymmetric Bets
# ==========================================================================

st.subheader("2. Top EV Asymmetric Bets")
st.caption("Markets in the 3-97% range scored by asymmetry, volume, and liquidity.")

ev_rows = []
for m in all_markets:
    yp = m["yes_price"]
    if yp is None or yp < 0.03 or yp > 0.97:
        continue
    if m["volume"] < max(filt_min_vol, 50_000):
        continue
    if m["liquidity"] < max(filt_min_liq, 5_000):
        continue

    score = asymmetry_score(yp, m["volume"], m["liquidity"])
    payout = 1.0 / yp if yp > 0 else 0
    side = "YES" if yp < 0.5 else "NO"
    price = yp if yp < 0.5 else (1.0 - yp)

    ev_rows.append(
        {
            "Question": m["question"],
            "Side": side,
            "Price": fmt_pct(price),
            "Payout": f"{1.0 / price:.1f}x" if price > 0 else "N/A",
            "Volume": fmt_usd(m["volume"]),
            "Liquidity": fmt_usd(m["liquidity"]),
            "Score": round(score, 2),
            "Link": polymarket_url(m["event_slug"]),
            "_market": m,
        }
    )

ev_rows.sort(key=lambda r: r["Score"], reverse=True)

if ev_rows:
    for i, row in enumerate(ev_rows[:20]):
        col1, col2, col3, col4, col5 = st.columns([4, 1, 1, 1, 1])
        col1.write(f"**{row['Question'][:80]}**")
        col2.write(f"{row['Side']} @ {row['Price']}")
        col3.write(row["Payout"])
        col4.write(row["Volume"])
        col5.write(f"Score: {row['Score']}")

        c1, c2, c3 = st.columns([1, 1, 4])
        if c1.button("Dive", key=f"ev_dd_{i}"):
            st.session_state.selected_market = row["_market"]["raw"]
            st.switch_page("pages/2_Deep_Dive.py")
        if c2.button("+Watch", key=f"ev_wl_{i}"):
            st.session_state.watchlist.append(make_watchlist_entry(row["_market"]))
            st.toast("Added to watchlist")
        st.divider()
else:
    st.info("No asymmetric bets found with current filters.")

# ==========================================================================
# Section 3: Contrarian Longshots
# ==========================================================================

st.subheader("3. Contrarian Longshots")
st.caption("Markets at 5-25% YES price with high volume - potential mispricings.")

longshot_rows = []
for m in all_markets:
    yp = m["yes_price"]
    if yp is None or yp < 0.05 or yp > 0.25:
        continue
    if m["volume"] < max(filt_min_vol, 100_000):
        continue

    longshot_rows.append(m)

longshot_rows.sort(key=lambda m: m["volume"], reverse=True)

if longshot_rows:
    display = []
    for m in longshot_rows[:20]:
        display.append(
            {
                "Question": m["question"][:80],
                "YES Price": fmt_pct(m["yes_price"]),
                "Payout": f"{1.0 / m['yes_price']:.1f}x" if m["yes_price"] > 0 else "N/A",
                "Volume": fmt_usd(m["volume"]),
                "Liquidity": fmt_usd(m["liquidity"]),
            }
        )
    st.dataframe(display, use_container_width=True)

    for i, m in enumerate(longshot_rows[:20]):
        c1, c2 = st.columns([1, 5])
        if c1.button("Dive", key=f"ls_dd_{i}"):
            st.session_state.selected_market = m["raw"]
            st.switch_page("pages/2_Deep_Dive.py")
        if c2.button("+Watch", key=f"ls_wl_{i}"):
            st.session_state.watchlist.append(make_watchlist_entry(m))
            st.toast("Added to watchlist")
else:
    st.info("No contrarian longshots found with current filters.")

# ==========================================================================
# Section 4: Closest Races
# ==========================================================================

st.subheader("4. Closest Races")
st.caption("Markets at 40-60% YES price with high volume - maximum uncertainty.")

race_rows = []
for m in all_markets:
    yp = m["yes_price"]
    if yp is None or yp < 0.40 or yp > 0.60:
        continue
    if m["volume"] < max(filt_min_vol, 100_000):
        continue

    race_rows.append(m)

race_rows.sort(key=lambda m: m["liquidity"], reverse=True)

if race_rows:
    display = []
    for m in race_rows[:20]:
        display.append(
            {
                "Question": m["question"][:80],
                "YES": fmt_pct(m["yes_price"]),
                "NO": fmt_pct(m["no_price"]),
                "Volume": fmt_usd(m["volume"]),
                "Liquidity": fmt_usd(m["liquidity"]),
            }
        )
    st.dataframe(display, use_container_width=True)

    for i, m in enumerate(race_rows[:20]):
        c1, c2 = st.columns([1, 5])
        if c1.button("Dive", key=f"rc_dd_{i}"):
            st.session_state.selected_market = m["raw"]
            st.switch_page("pages/2_Deep_Dive.py")
        if c2.button("+Watch", key=f"rc_wl_{i}"):
            st.session_state.watchlist.append(make_watchlist_entry(m))
            st.toast("Added to watchlist")
else:
    st.info("No close races found with current filters.")
