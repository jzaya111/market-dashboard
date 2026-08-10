"""
Daily Capital Markets & Economy Dashboard — data fetch + render script.

Run this locally (with FRED_API_KEY set) to test, or let the GitHub Actions
workflow (.github/workflows/daily-update.yml) run it on a schedule.

Data sources (all free):
- FRED API (fred.stlouisfed.org)  -> yields, CPI, unemployment, payrolls, fed funds
- Yahoo Finance (via yfinance)    -> indices, VIX, oil, gold
- RSS feeds                        -> capital markets headlines

Output: docs/index.html (served by GitHub Pages)
"""

import os
import sys
import datetime
import requests
import feedparser
import yfinance as yf
from jinja2 import Environment, FileSystemLoader

FRED_API_KEY = os.environ.get("FRED_API_KEY")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

if not FRED_API_KEY:
    print("ERROR: FRED_API_KEY environment variable is not set.", file=sys.stderr)
    print("Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# FRED helpers
# ---------------------------------------------------------------------------

def fred_latest(series_id, units=None, n=2):
    """Return the last n non-missing observations for a FRED series,
    most recent last: [(date, value), ...]"""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 30,  # pull extra in case of missing '.' values
    }
    if units:
        params["units"] = units
    resp = requests.get(FRED_BASE, params=params, timeout=20)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    clean = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
    clean.reverse()  # oldest -> newest
    return clean[-n:]


def fred_value(series_id, units=None):
    """Most recent single value for a series."""
    data = fred_latest(series_id, units=units, n=1)
    return data[-1][1] if data else None


# ---------------------------------------------------------------------------
# Yahoo Finance helpers
# ---------------------------------------------------------------------------

def yf_quote(ticker):
    """Return (last_close, pct_change_vs_prior_close)."""
    hist = yf.Ticker(ticker).history(period="5d")
    if len(hist) < 2:
        return None, 0.0
    last = hist["Close"].iloc[-1]
    prev = hist["Close"].iloc[-2]
    pct = (last - prev) / prev * 100
    return round(float(last), 2), round(float(pct), 2)


# ---------------------------------------------------------------------------
# Headlines via RSS (no API key required)
# ---------------------------------------------------------------------------

FEEDS = [
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
]


def fetch_headlines(limit=5):
    items = []
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                items.append({
                    "source": source,
                    "title": entry.title,
                    "link": entry.link,
                })
        except Exception as e:
            print(f"WARN: failed to fetch {source}: {e}", file=sys.stderr)
    return items[:limit]


# ---------------------------------------------------------------------------
# Business cycle heuristic
# ---------------------------------------------------------------------------
# NOTE: This is a simplified rule-based read, not an official NBER dating.
# Adjust thresholds to taste — it's meant as a starting signal, not a verdict.

PHASE_GEOMETRY = {
    # phase_id: (x on the 520-wide cycle svg, approx y on the curve path)
    0: (58, 90),    # Initial Recovery
    1: (160, 50),   # Early Expansion
    2: (260, 65),   # Late Expansion
    3: (360, 130),  # Slowdown
    4: (460, 155),  # Contraction
}
PHASE_NAMES = {
    0: "Initial Recovery",
    1: "Early Expansion",
    2: "Late Expansion",
    3: "Slowdown",
    4: "Contraction",
}


def classify_cycle(spread_bp, payrolls_k, cpi_yoy, cpi_yoy_prior, unrate, unrate_prior):
    cooling_labor = payrolls_k < 75
    strong_labor = payrolls_k >= 150
    rising_unemployment = unrate > unrate_prior
    disinflating = cpi_yoy < cpi_yoy_prior
    inverted = spread_bp < 0

    if inverted and rising_unemployment:
        phase_id = 4  # Contraction
    elif cooling_labor and (disinflating or rising_unemployment):
        phase_id = 3  # Slowdown
    elif strong_labor and spread_bp >= 50 and not disinflating:
        phase_id = 1  # Early Expansion
    elif not cooling_labor and spread_bp >= 20:
        phase_id = 2  # Late Expansion
    else:
        phase_id = 0  # Initial Recovery

    descriptions = {
        0: "Rates and yields are low, and stocks/cyclical assets are attracting early inflows as growth troughs and turns up.",
        1: "Short-term rates are moving up off the bottom, the yield curve is flattening, and stocks are trending upward.",
        2: "Short-term rates are rising and long-term rates rise more slowly; credit conditions tighten and stocks get more volatile.",
        3: "Short-term rates have peaked, bond yields are topping out, and the curve may invert; credit spreads widen and cyclical stocks lag.",
        4: "Short-term rates are dropping, bond yields decline, and the curve re-steepens; stocks look to bottom late in this phase.",
    }

    tags = [
        {"text": f"Fed funds trend", "hot": False},
        {"text": f"Payrolls {payrolls_k:+d}k", "hot": cooling_labor},
        {"text": f"CPI {'easing' if disinflating else 'firming'} to {cpi_yoy:.1f}%", "hot": False},
        {"text": f"Curve {'inverted' if inverted else 'normal'}", "hot": inverted},
    ]

    x, y = PHASE_GEOMETRY[phase_id]
    return {
        "phase_id": phase_id,
        "phase": PHASE_NAMES[phase_id],
        "desc": descriptions[phase_id],
        "tags": tags,
        "marker_x": x,
        "marker_y": y,
    }


# ---------------------------------------------------------------------------
# Yield curve SVG
# ---------------------------------------------------------------------------

MATURITIES = [
    ("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"),
    ("2Y", "DGS2"), ("3Y", "DGS3"), ("5Y", "DGS5"), ("7Y", "DGS7"),
    ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30"),
]


def build_yield_curve_svg(values):
    """values: list of (label, yield_pct) in maturity order."""
    xs = [50 + i * 40 for i in range(len(values))]
    ys_vals = [v for _, v in values]
    lo, hi = min(ys_vals) - 0.15, max(ys_vals) + 0.15
    top, bottom = 15, 150

    def scale_y(v):
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    points = []
    circles = []
    labels = []
    for (label, val), x in zip(values, xs):
        y = scale_y(val)
        points.append(f"{x},{y:.0f}")
        highlight = label == "10Y"
        r = 4 if highlight else 3
        circles.append(
            f'<circle cx="{x}" cy="{y:.0f}" r="{r}" '
            f'{"fill=\"#fff\" stroke=\"#D4A24C\" stroke-width=\"2\"" if highlight else "fill=\"#D4A24C\""}/>'
        )
        color = "#D4A24C" if highlight else "#8A93A6"
        weight = "600" if highlight else "400"
        labels.append(
            f'<text x="{x}" y="168" text-anchor="middle" fill="{color}" '
            f'font-family="IBM Plex Mono" font-size="9" font-weight="{weight}">{label}</text>'
        )

    polyline = f'<polyline fill="none" stroke="#D4A24C" stroke-width="2.5" points="{" ".join(points)}" />'
    svg = f'''<svg viewBox="0 0 480 190" style="width:100%;height:auto;">
        <line x1="35" y1="150" x2="470" y2="150" stroke="#232935" stroke-width="1"/>
        <line x1="35" y1="10" x2="35" y2="150" stroke="#232935" stroke-width="1"/>
        <text x="10" y="{top+15}" fill="#8A93A6" font-family="IBM Plex Mono" font-size="9">{hi:.1f}%</text>
        <text x="10" y="{(top+bottom)//2}" fill="#8A93A6" font-family="IBM Plex Mono" font-size="9">{(hi+lo)/2:.1f}%</text>
        <text x="10" y="{bottom}" fill="#8A93A6" font-family="IBM Plex Mono" font-size="9">{lo:.1f}%</text>
        {polyline}
        {''.join(circles)}
        {''.join(labels)}
    </svg>'''
    return svg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching FRED data...")
    yields_raw = [(label, fred_value(series)) for label, series in MATURITIES]
    yields_raw = [(l, v) for l, v in yields_raw if v is not None]

    y2 = dict(yields_raw).get("2Y")
    y10 = dict(yields_raw).get("10Y")
    y30 = dict(yields_raw).get("30Y")
    spread_bp = round((y10 - y2) * 100) if (y10 is not None and y2 is not None) else 0

    cpi_series = fred_latest("CPIAUCSL", units="pc1", n=2)
    cpi = cpi_series[-1][1] if cpi_series else 0.0
    cpi_prior = cpi_series[0][1] if len(cpi_series) > 1 else cpi

    unrate_series = fred_latest("UNRATE", n=2)
    unrate = unrate_series[-1][1] if unrate_series else 0.0
    unrate_prior = unrate_series[0][1] if len(unrate_series) > 1 else unrate

    payrolls_series = fred_latest("PAYEMS", units="chg", n=1)
    payrolls_k = round(payrolls_series[-1][1]) if payrolls_series else 0

    fedfunds = round(fred_value("FEDFUNDS") or 0, 2)

    print("Fetching Yahoo Finance data...")
    ticker_defs = [
        ("S&P 500", "^GSPC"),
        ("Dow Jones", "^DJI"),
        ("Nasdaq 100", "^NDX"),
        ("10Y Yield", None),  # filled from FRED below
        ("WTI Crude", "CL=F"),
        ("VIX", "^VIX"),
    ]
    tickers = []
    for label, sym in ticker_defs:
        if sym is None:
            tickers.append({"label": label, "value": f"{y10:.2f}%", "pct": 0.0})
            continue
        val, pct = yf_quote(sym)
        if val is None:
            tickers.append({"label": label, "value": "n/a", "pct": 0.0})
        else:
            fmt = f"${val:,.2f}" if sym == "CL=F" else f"{val:,.2f}"
            tickers.append({"label": label, "value": fmt, "pct": pct})

    print("Classifying business cycle phase...")
    cycle = classify_cycle(spread_bp, payrolls_k, cpi, cpi_prior, unrate, unrate_prior)

    print("Fetching headlines...")
    headlines = fetch_headlines(limit=5)

    print("Building yield curve chart...")
    yc_svg = build_yield_curve_svg(yields_raw)

    now = datetime.datetime.utcnow()
    context = {
        "date_str": now.strftime("%A, %B %d, %Y"),
        "generated_at": now.strftime("%H:%M"),
        "tickers": tickers,
        "cycle": cycle,
        "spread_bp": spread_bp,
        "yields": {"y2": f"{y2:.2f}", "y10": f"{y10:.2f}", "y30": f"{y30:.2f}"},
        "fedfunds": fedfunds,
        "econ": {
            "cpi": round(cpi, 2), "cpi_prior": round(cpi_prior, 2),
            "unrate": round(unrate, 2), "unrate_prior": round(unrate_prior, 2),
            "payrolls": payrolls_k,
        },
        "headlines": headlines,
        "yield_curve_svg": yc_svg,
    }

    env = Environment(loader=FileSystemLoader("."))
    template = env.get_template("template.html")
    output = template.render(**context)

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(output)

    print("Wrote docs/index.html")


if __name__ == "__main__":
    main()
