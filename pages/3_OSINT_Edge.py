"""
OSINT Edge Finder - 5 tabs of market intelligence.

1. News Cross-Reference
2. Volume Anomalies
3. Resolution Timeline
4. Smart Money Flow
5. Cross-Market Divergence
"""

import re
from datetime import datetime, timedelta, timezone

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils import (
    cached_fetch_events,
    cached_fetch_markets,
    cached_fetch_trades,
    cached_search_markets,
    fetch_news_rss,
    get_provider,
    parse_market,
    is_non_exclusive,
    sanitize_md,
    fmt_pct,
    fmt_usd,
    days_until,
)

st.header("OSINT Edge Finder")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "News Cross-Reference",
        "Volume Anomalies",
        "Resolution Timeline",
        "Smart Money Flow",
        "Cross-Market Divergence",
    ]
)

# ==========================================================================
# Helper: Extract keywords from market questions
# ==========================================================================


def extract_keywords(question: str) -> list[str]:
    """Extract proper nouns and key terms from a market question."""
    # Remove common filler words
    stopwords = {
        "will", "the", "be", "in", "on", "at", "to", "by", "for", "of",
        "a", "an", "is", "are", "was", "were", "has", "have", "had",
        "this", "that", "these", "those", "it", "its", "and", "or",
        "but", "not", "no", "yes", "do", "does", "did", "can", "could",
        "would", "should", "may", "might", "shall", "with", "from",
        "before", "after", "above", "below", "between", "during",
        "than", "more", "less", "most", "least", "any", "all", "each",
        "every", "both", "either", "neither", "other", "another",
        "what", "which", "who", "whom", "when", "where", "how", "why",
    }

    # Get capitalized words (proper nouns)
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', question)
    # Also get remaining significant words
    all_words = re.findall(r'\b\w{3,}\b', question.lower())
    significant = [w for w in all_words if w not in stopwords]

    return list(dict.fromkeys(words + significant))[:5]  # dedupe, max 5


# ==========================================================================
# Tab 1: News Cross-Reference
# ==========================================================================

with tab1:
    st.subheader("News Cross-Reference")
    st.caption("Match Google News headlines to active markets.")

    num_markets_news = st.slider(
        "Markets to scan", 10, 100, 50, key="news_num"
    )

    if st.button("Fetch News Matches", key="news_btn"):
        with st.spinner("Fetching markets and news..."):
            markets_raw = cached_fetch_markets(
                active=True, limit=num_markets_news, order="volume", ascending=False
            )

            matches = []
            for mr in markets_raw:
                m = parse_market(mr)
                if m["volume"] < 10_000:
                    continue

                keywords = extract_keywords(m["question"])
                if not keywords:
                    continue

                query = " ".join(keywords[:3])
                news = fetch_news_rss(query, max_results=5)

                if news:
                    matches.append(
                        {
                            "market": m,
                            "keywords": keywords,
                            "news": news,
                        }
                    )

            st.session_state["news_matches"] = matches

    news_matches = st.session_state.get("news_matches", [])

    if news_matches:
        for match in news_matches:
            m = match["market"]
            with st.expander(
                f"{m['question'][:80]} | {fmt_pct(m['yes_price'])}"
            ):
                st.write(f"**Keywords**: {', '.join(match['keywords'])}")
                st.write(f"**Price**: {fmt_pct(m['yes_price'])} | **Volume**: {fmt_usd(m['volume'])}")

                for article in match["news"]:
                    pub = article.get("published", "")
                    source = article.get("source", "")
                    age_str = ""

                    # Parse age for color coding
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub)
                        age_hours = (
                            datetime.now(timezone.utc) - pub_dt
                        ).total_seconds() / 3600
                        if age_hours < 1:
                            age_str = ":green[< 1 hour ago]"
                        elif age_hours < 24:
                            age_str = f":orange[{int(age_hours)}h ago]"
                        else:
                            age_str = f":gray[{int(age_hours / 24)}d ago]"
                    except Exception:
                        age_str = pub

                    title = sanitize_md(article["title"])
                    link = article["link"]
                    source_safe = sanitize_md(source)
                    st.markdown(
                        f"- [{title}]({link}) {age_str} *({source_safe})*"
                    )
    elif "news_matches" in st.session_state:
        st.info("No news matches found.")

# ==========================================================================
# Tab 2: Volume Anomalies
# ==========================================================================

with tab2:
    st.subheader("Volume Anomalies")
    st.caption(
        "Markets where 24h volume is 3x+ the average daily volume."
    )

    vol_threshold = st.slider(
        "Volume ratio threshold", 2.0, 10.0, 3.0, 0.5, key="vol_thresh"
    )

    if st.button("Scan Volume", key="vol_btn"):
        with st.spinner("Scanning..."):
            markets_raw = cached_fetch_markets(
                active=True, limit=200, order="volume", ascending=False
            )

            anomalies = []
            now = datetime.now(timezone.utc)

            for mr in markets_raw:
                m = parse_market(mr)
                if m["volume"] < 10_000:
                    continue

                # Estimate daily avg from total volume and market age
                created = mr.get("createdAt") or mr.get("startDate")
                if not created:
                    continue

                try:
                    created_dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    days_active = max(
                        (now - created_dt).total_seconds() / 86400, 1
                    )
                except (ValueError, TypeError):
                    continue

                avg_daily = m["volume"] / days_active

                # Use volume24hr if available, otherwise estimate
                vol_24h = 0.0
                for key in ("volume24hr", "volume24Hr"):
                    val = mr.get(key)
                    if val is not None:
                        try:
                            vol_24h = float(val)
                            break
                        except (ValueError, TypeError):
                            pass

                if vol_24h <= 0 or avg_daily <= 0:
                    continue

                ratio = vol_24h / avg_daily

                if ratio >= vol_threshold:
                    anomalies.append(
                        {
                            "Question": m["question"][:80],
                            "24h Vol": fmt_usd(vol_24h),
                            "Avg Daily": fmt_usd(avg_daily),
                            "Ratio": f"{ratio:.1f}x",
                            "YES Price": fmt_pct(m["yes_price"]),
                            "Total Vol": fmt_usd(m["volume"]),
                            "_ratio": ratio,
                        }
                    )

            anomalies.sort(key=lambda r: r["_ratio"], reverse=True)
            st.session_state["vol_anomalies"] = anomalies

    vol_anomalies = st.session_state.get("vol_anomalies", [])
    if vol_anomalies:
        display = [
            {k: v for k, v in a.items() if not k.startswith("_")}
            for a in vol_anomalies[:30]
        ]
        st.dataframe(display, use_container_width=True)
    elif "vol_anomalies" in st.session_state:
        st.info("No volume anomalies found.")

# ==========================================================================
# Tab 3: Resolution Timeline
# ==========================================================================

with tab3:
    st.subheader("Resolution Timeline")
    st.caption("Markets resolving soon - faster resolution = faster compounding.")

    max_days = st.slider("Max days until resolution", 1, 30, 7, key="timeline_days")

    if st.button("Scan Timelines", key="timeline_btn"):
        with st.spinner("Scanning..."):
            markets_raw = cached_fetch_markets(
                active=True, limit=200, order="volume", ascending=False
            )

            timeline_rows = []
            now = datetime.now(timezone.utc)

            for mr in markets_raw:
                m = parse_market(mr)
                du = days_until(m["end_date"])
                if du is None or du > max_days or du <= 0:
                    continue
                if m["volume"] < 5_000:
                    continue

                timeline_rows.append(
                    {
                        "question": m["question"][:80],
                        "days_until": du,
                        "yes_price": m["yes_price"] or 0.5,
                        "volume": m["volume"],
                        "liquidity": m["liquidity"],
                    }
                )

            st.session_state["timeline_rows"] = timeline_rows

    timeline_rows = st.session_state.get("timeline_rows", [])

    if timeline_rows:
        # Scatter: days_until vs price, size=volume
        fig = px.scatter(
            timeline_rows,
            x="days_until",
            y="yes_price",
            size="volume",
            hover_name="question",
            color="days_until",
            color_continuous_scale="RdYlGn_r",
            labels={
                "days_until": "Days Until Resolution",
                "yes_price": "YES Price",
                "volume": "Volume",
            },
        )
        fig.update_layout(
            height=500,
            yaxis=dict(range=[0, 1], tickformat=".0%"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Table
        display = [
            {
                "Question": r["question"],
                "Days": r["days_until"],
                "YES Price": fmt_pct(r["yes_price"]),
                "Volume": fmt_usd(r["volume"]),
                "Liquidity": fmt_usd(r["liquidity"]),
            }
            for r in sorted(timeline_rows, key=lambda r: r["days_until"])
        ]
        st.dataframe(display, use_container_width=True)
    elif "timeline_rows" in st.session_state:
        st.info("No markets resolving in the selected timeframe.")

# ==========================================================================
# Tab 4: Smart Money Flow
# ==========================================================================

with tab4:
    st.subheader("Smart Money Flow")
    st.caption("Analyze trade flow for directional signals.")

    sm_query = st.text_input(
        "Search market for trade analysis", key="sm_search"
    )

    if sm_query:
        results = cached_search_markets(sm_query, limit=5)
        if results:
            options = {
                r.get("question", r.get("title", "?"))[:80]: r for r in results
            }
            selected = st.selectbox(
                "Select market", list(options.keys()), key="sm_select"
            )
            market_raw = options.get(selected)
        else:
            market_raw = None
            st.warning("No markets found.")
    else:
        market_raw = None

    if market_raw:
        m = parse_market(market_raw)
        cond_id = m["condition_id"]

        if cond_id and st.button("Analyze Trade Flow", key="sm_btn"):
            with st.spinner("Fetching trades..."):
                # Fetch up to 500 trades across multiple pages
                all_trades = []
                for offset in range(0, 500, 100):
                    batch = cached_fetch_trades(
                        market=cond_id, limit=100, offset=offset
                    )
                    if not batch:
                        break
                    all_trades.extend(batch)

            if all_trades:
                sizes = []
                buy_volume = 0.0
                sell_volume = 0.0

                for t in all_trades:
                    size = float(t.get("size", 0))
                    side = t.get("side", "").lower()
                    sizes.append(size)
                    if side == "buy":
                        buy_volume += size
                    elif side == "sell":
                        sell_volume += size

                avg_size = sum(sizes) / len(sizes) if sizes else 0
                large_threshold = avg_size * 2

                large_buys = sum(
                    1 for t in all_trades
                    if float(t.get("size", 0)) >= large_threshold
                    and t.get("side", "").lower() == "buy"
                )
                large_sells = sum(
                    1 for t in all_trades
                    if float(t.get("size", 0)) >= large_threshold
                    and t.get("side", "").lower() == "sell"
                )

                # Metrics
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Trades", len(all_trades))
                c2.metric("Avg Size", fmt_usd(avg_size))
                c3.metric("Buy Volume", fmt_usd(buy_volume))
                c4.metric("Sell Volume", fmt_usd(sell_volume))

                # Directional signal
                total_vol = buy_volume + sell_volume
                if total_vol > 0:
                    buy_pct = buy_volume / total_vol
                    if buy_pct > 0.6:
                        st.success(
                            f"Bullish signal: {buy_pct:.0%} buy volume"
                        )
                    elif buy_pct < 0.4:
                        st.error(
                            f"Bearish signal: {1 - buy_pct:.0%} sell volume"
                        )
                    else:
                        st.info(f"Neutral: {buy_pct:.0%} buy / {1 - buy_pct:.0%} sell")

                # Large trades
                st.write(
                    f"**Large trades** (>= {fmt_usd(large_threshold)}): "
                    f"{large_buys} buys, {large_sells} sells"
                )

                # Trade size histogram
                fig_hist = px.histogram(
                    x=sizes,
                    nbins=30,
                    labels={"x": "Trade Size ($)", "y": "Count"},
                    title="Trade Size Distribution",
                )
                fig_hist.update_layout(height=300)
                st.plotly_chart(fig_hist, use_container_width=True)

                # Trade timeline
                trade_times = []
                for t in all_trades:
                    ts = t.get("timestamp") or t.get("created_at")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(
                                str(ts).replace("Z", "+00:00")
                            )
                            trade_times.append(
                                {
                                    "time": dt,
                                    "size": float(t.get("size", 0)),
                                    "side": t.get("side", "unknown"),
                                }
                            )
                        except (ValueError, TypeError):
                            pass

                if trade_times:
                    fig_tl = px.scatter(
                        trade_times,
                        x="time",
                        y="size",
                        color="side",
                        color_discrete_map={"buy": "#22c55e", "sell": "#ef4444"},
                        title="Trade Timeline",
                    )
                    fig_tl.update_layout(height=300)
                    st.plotly_chart(fig_tl, use_container_width=True)
            else:
                st.info("No trades found for this market.")

# ==========================================================================
# Tab 5: Cross-Market Divergence
# ==========================================================================

with tab5:
    st.subheader("Cross-Market Divergence")
    st.caption(
        "Multi-outcome events where probabilities diverge from 1.0."
    )

    if st.button("Scan Divergences", key="div_btn"):
        with st.spinner("Scanning events..."):
            all_events = []
            for offset in range(0, 200, 100):
                batch = cached_fetch_events(
                    active=True, limit=100, offset=offset, order="volume", ascending=False
                )
                if not batch:
                    break
                all_events.extend(batch)

            divergences = []
            for event in all_events:
                markets = event.get("markets", [])
                if len(markets) < 3:
                    continue

                parsed = [parse_market(m) for m in markets]

                # Skip non-exclusive
                if any(is_non_exclusive(p["question"]) for p in parsed):
                    continue

                prices = [
                    p["yes_price"] for p in parsed if p["yes_price"] is not None
                ]
                if not prices:
                    continue

                prob_sum = sum(prices)
                deviation = abs(prob_sum - 1.0)

                if deviation > 0.02:
                    divergences.append(
                        {
                            "event": event.get("title", "?"),
                            "outcomes": len(parsed),
                            "prob_sum": prob_sum,
                            "deviation": deviation,
                            "markets": parsed,
                        }
                    )

            divergences.sort(key=lambda d: d["deviation"], reverse=True)
            st.session_state["divergences"] = divergences

    divergences = st.session_state.get("divergences", [])

    if divergences:
        for div in divergences[:15]:
            with st.expander(
                f"{div['event']} | Sum={div['prob_sum']:.3f} | "
                f"Dev={div['deviation']:.3f}"
            ):
                for m in div["markets"]:
                    st.write(
                        f"- {m['question'][:60]}: **{fmt_pct(m['yes_price'])}** "
                        f"({fmt_usd(m['volume'])} vol)"
                    )
    elif "divergences" in st.session_state:
        st.info("No significant divergences found.")
