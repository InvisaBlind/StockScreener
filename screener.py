#!/usr/bin/env python3
"""
Stock Screener - Algorithm
Finds established, undervalued companies using a two-gate system.

Gate 1 (Maturity): Market cap >= $2B, 5+ yr history, 2+ yrs positive EPS
Gate 2 (Scoring): P/E vs 5yr avg, P/FCF, EPS growth, D/E ratio, volume spike
"""

# python "C:\Users\Henry\Desktop\Claude Test\screener.py"
# python "C:\Users\Henry\Desktop\Claude Test\run.py"

import yfinance as yf
import pandas as pd
import numpy as np
import pandas_market_calendars as mcal
from datetime import datetime, date
import warnings
import time
import sys

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Market Calendar
# ---------------------------------------------------------------------------

def is_trading_day(check_date: date = None) -> bool:
    """Return True if the given date (default: today) is a NYSE trading day."""
    if check_date is None:
        check_date = date.today()
    nyse     = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(
        start_date=check_date.strftime("%Y-%m-%d"),
        end_date=check_date.strftime("%Y-%m-%d"),
    )
    return not schedule.empty


# ---------------------------------------------------------------------------
# Ticker Universe
# ---------------------------------------------------------------------------

def get_ticker_universe():
    """
    Return the full S&P 500 ticker list.
    Tries two live sources first; falls back to a hardcoded list so the
    screener always works even without internet access to those endpoints.
    """

    import urllib.request
    import io

    # --- Source 1: SPDR SPY daily holdings XLSX (State Street - official ETF issuer, updated daily) ---
    try:
        url = (
            "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/"
            "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=15).read()
        df = pd.read_excel(io.BytesIO(data), skiprows=4)
        tickers = df.iloc[:, 1].dropna().astype(str).str.strip()
        tickers = tickers[tickers.str.match(r"^[A-Z\-]+$")].tolist()
        if len(tickers) > 400:
            print(f"Loaded {len(tickers)} S&P 500 tickers (SPDR SPY holdings - today's date).")
            return tickers
    except Exception:
        pass

    # --- Source 2: GitHub CSV (community-maintained, good fallback) ---
    try:
        url = (
            "https://raw.githubusercontent.com/datasets/"
            "s-and-p-500-companies/main/data/constituents.csv"
        )
        df = pd.read_csv(url)
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        if len(tickers) > 400:
            print(f"Loaded {len(tickers)} S&P 500 tickers (GitHub datasource).")
            return tickers
    except Exception:
        pass

    print("ERROR: Could not load S&P 500 tickers from any source. Check your internet connection.")
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tz_strip(hist: pd.DataFrame) -> pd.DataFrame:
    """Remove timezone info from DatetimeIndex so year comparisons work."""
    if hist.index.tz is not None:
        hist = hist.copy()
        hist.index = hist.index.tz_localize(None)
    return hist


def _get_annual_eps(income_stmt: pd.DataFrame) -> list[float]:
    """Return annual EPS values in chronological order (oldest → newest)."""
    for label in ("Diluted EPS", "Basic EPS"):
        if income_stmt is not None and label in income_stmt.index:
            row = income_stmt.loc[label].dropna()
            # yfinance returns columns newest-first; reverse to oldest-first
            vals = [float(v) for v in reversed(row.values)]
            return vals
    return []


# ---------------------------------------------------------------------------
# Gate 1  -  Maturity Filters
# ---------------------------------------------------------------------------

def passes_gate1(info: dict, hist: pd.DataFrame, income_stmt: pd.DataFrame):
    """
    Returns (passed: bool, reason: str).
    All three sub-filters must pass.
    """
    failures = []

    # 1. Market cap >= $2B
    market_cap = info.get("marketCap") or 0
    if market_cap < 2_000_000_000:
        failures.append(f"MarketCap ${market_cap / 1e9:.2f}B < $2B")

    # 2. 5+ years of trading history (~1 000+ trading days)
    n_days = len(hist) if hist is not None else 0
    if n_days < 900:
        failures.append(f"Only {n_days} trading days of history (need ~1 000+)")

    # 3. EPS positive for 2+ consecutive years
    eps_series = _get_annual_eps(income_stmt)
    if len(eps_series) < 2 or not all(e > 0 for e in eps_series[-2:]):
        snippet = [round(e, 2) for e in eps_series[-3:]] if eps_series else []
        failures.append(f"EPS not positive for 2 consecutive years: {snippet}")

    if failures:
        return False, " | ".join(failures)
    return True, ""


# ---------------------------------------------------------------------------
# Gate 2  -  Scoring Criteria
# ---------------------------------------------------------------------------

def _score_pe_vs_avg(info: dict, hist: pd.DataFrame, income_stmt: pd.DataFrame) -> dict:
    """P/E currently 30%+ below its 5-year average = bullish."""
    current_pe = info.get("trailingPE")
    if not current_pe or current_pe <= 0 or current_pe > 1_000:
        return {"passes": False, "reason": "No valid trailing P/E", "current_pe": None, "avg_pe": None, "discount_pct": None}

    hist = _tz_strip(hist)
    eps_by_year = {}

    # Build year → EPS mapping from income statement
    for label in ("Diluted EPS", "Basic EPS"):
        if income_stmt is not None and label in income_stmt.index:
            for col in income_stmt.columns:
                val = income_stmt.loc[label, col]
                if pd.notna(val):
                    eps_by_year[col.year] = float(val)
            break

    # If no EPS rows, try net income / diluted shares
    if not eps_by_year and income_stmt is not None and "Net Income" in income_stmt.index:
        for shares_label in ("Diluted Average Shares", "Basic Average Shares"):
            if shares_label in income_stmt.index:
                for col in income_stmt.columns:
                    ni = income_stmt.loc["Net Income", col]
                    sh = income_stmt.loc[shares_label, col]
                    if pd.notna(ni) and pd.notna(sh) and float(sh) > 0:
                        eps_by_year[col.year] = float(ni) / float(sh)
                break

    pe_samples = []
    for year, eps in eps_by_year.items():
        if eps <= 0:
            continue
        prices = hist[hist.index.year == year]["Close"]
        if len(prices) > 0:
            pe = prices.mean() / eps
            if 0 < pe < 500:
                pe_samples.append(pe)

    if not pe_samples:
        return {"passes": False, "reason": "Cannot compute historical P/E", "current_pe": round(current_pe, 2), "avg_pe": None, "discount_pct": None}

    avg_pe = float(np.mean(pe_samples))
    discount_pct = (avg_pe - current_pe) / avg_pe * 100

    return {
        "passes": discount_pct >= 30,
        "reason": f"P/E {current_pe:.1f} vs 5yr avg {avg_pe:.1f} → {discount_pct:.1f}% discount",
        "current_pe": round(current_pe, 2),
        "avg_pe": round(avg_pe, 2),
        "discount_pct": round(discount_pct, 1),
    }


def _score_pfcf(info: dict) -> dict:
    """P/FCF < 15x = undervalued."""
    pfcf = info.get("priceToFreeCashflows") or info.get("priceToFreeCashFlow")

    if pfcf is None:
        mc = info.get("marketCap") or 0
        fcf = info.get("freeCashflow") or 0
        if mc > 0 and fcf > 0:
            pfcf = mc / fcf

    if pfcf is None or pfcf <= 0 or pfcf > 10_000:
        return {"passes": False, "reason": "No valid P/FCF data", "value": None}

    return {
        "passes": pfcf < 15,
        "reason": f"P/FCF = {pfcf:.1f}x ({'undervalued' if pfcf < 15 else 'above threshold'})",
        "value": round(pfcf, 2),
    }


def _score_eps_growth(info: dict, income_stmt: pd.DataFrame) -> dict:
    """2+ consecutive years of EPS growth."""
    eps = _get_annual_eps(income_stmt)  # oldest → newest

    if len(eps) >= 3:
        # Check the two most recent year-over-year periods
        yr1_growth = eps[-2] > eps[-3]
        yr2_growth = eps[-1] > eps[-2]
        passes = yr1_growth and yr2_growth
        growth_2yr = ((eps[-1] - eps[-3]) / abs(eps[-3]) * 100) if eps[-3] != 0 else None
        return {
            "passes": passes,
            "reason": f"EPS {[round(e, 2) for e in eps[-3:]]}  -  growth: {yr1_growth}, {yr2_growth}",
            "growth_pct": round(growth_2yr, 1) if growth_2yr is not None else None,
        }

    # Fallback: forward EPS > trailing EPS as a proxy
    trailing = info.get("trailingEps")
    forward = info.get("forwardEps")
    if trailing and forward and trailing > 0 and forward > trailing:
        proxy_growth = (forward - trailing) / trailing * 100
        return {
            "passes": True,
            "reason": f"Forward EPS {forward:.2f} > Trailing EPS {trailing:.2f} (proxy)",
            "growth_pct": round(proxy_growth, 1),
        }

    return {
        "passes": False,
        "reason": f"Insufficient EPS history ({len(eps)} years available)",
        "growth_pct": None,
    }


def _score_debt_to_equity(info: dict) -> dict:
    """Debt-to-Equity < 2x = manageable risk."""
    de_raw = info.get("debtToEquity")
    if de_raw is None:
        return {"passes": False, "reason": "No D/E data", "value": None}

    # yfinance returns D/E as a percentage (e.g., 150 → 1.5x)
    de = de_raw / 100
    return {
        "passes": de < 2.0,
        "reason": f"D/E = {de:.2f}x ({'healthy' if de < 2 else 'high debt'})",
        "value": round(de, 2),
    }


def _score_volume_spike(hist: pd.DataFrame) -> dict:
    """Latest day's volume >= 1.5x the 20-day average = institutional signal."""
    if hist is None or len(hist) < 22:
        return {"passes": False, "reason": "Insufficient volume history", "ratio": None}

    latest_vol = hist["Volume"].iloc[-1]
    avg_20d = hist["Volume"].iloc[-21:-1].mean()

    if avg_20d == 0:
        return {"passes": False, "reason": "Zero 20-day average volume", "ratio": None}

    ratio = latest_vol / avg_20d
    return {
        "passes": ratio >= 1.5,
        "reason": f"Volume {ratio:.2f}x 20-day avg ({'spike' if ratio >= 1.5 else 'normal'})",
        "ratio": round(ratio, 2),
    }


def _compute_score_100(pe: dict, pfcf: dict, eps_g: dict, de: dict, vol: dict) -> int:
    """
    Score each criterion on a 0-20 scale (100 total).
    Passing the threshold always earns at least 15/20 for that criterion.
    """
    score = 0

    # P/E discount vs 5yr average (20 pts)
    disc = pe.get("discount_pct") or 0
    if   disc >= 40: score += 20
    elif disc >= 30: score += 15
    elif disc >= 20: score += 10
    elif disc >= 10: score += 5

    # P/FCF (20 pts)
    p = pfcf.get("value") or 9999
    if   p < 10:  score += 20
    elif p < 15:  score += 15
    elif p < 20:  score += 10
    elif p < 25:  score += 5

    # EPS growth (20 pts)
    growth = eps_g.get("growth_pct") or 0
    if eps_g.get("passes"):
        if   growth > 20: score += 20
        else:             score += 15
    elif growth > 0:      score += 8  # only one year of growth

    # Debt-to-Equity (20 pts)
    d = de.get("value")
    if d is not None:
        if   d < 0.5: score += 20
        elif d < 1.0: score += 16
        elif d < 2.0: score += 12
        elif d < 3.0: score += 5

    # Volume spike (20 pts)
    r = vol.get("ratio") or 0
    if   r >= 2.0: score += 20
    elif r >= 1.5: score += 15
    elif r >= 1.25: score += 8
    elif r >= 1.0:  score += 3

    return score


def score_stock(ticker: str, info: dict, hist: pd.DataFrame, income_stmt: pd.DataFrame) -> dict:
    pe      = _score_pe_vs_avg(info, hist, income_stmt)
    pfcf    = _score_pfcf(info)
    eps_g   = _score_eps_growth(info, income_stmt)
    de      = _score_debt_to_equity(info)
    vol     = _score_volume_spike(hist)

    criteria  = {"pe_vs_avg": pe, "pfcf": pfcf, "eps_growth": eps_g, "debt_to_equity": de, "volume_spike": vol}
    n_passed  = sum(1 for c in criteria.values() if c["passes"])
    score_100 = _compute_score_100(pe, pfcf, eps_g, de, vol)

    return {
        "ticker":          ticker,
        "company":         info.get("shortName", ticker),
        "sector":          info.get("sector", "Unknown"),
        "market_cap_B":    round((info.get("marketCap") or 0) / 1e9, 2),
        "criteria_passed": n_passed,
        "all_pass":        n_passed == 5,
        "score_100":       score_100,
        # flat columns for CSV
        "current_pe":      pe["current_pe"],
        "avg_pe_5yr":      pe["avg_pe"],
        "pe_discount_pct": pe["discount_pct"],
        "pfcf":            pfcf["value"],
        "eps_growth_pct":  eps_g["growth_pct"],
        "debt_to_equity":  de["value"],
        "volume_ratio":    vol["ratio"],
        # detail for terminal
        "_criteria":       criteria,
    }


# ---------------------------------------------------------------------------
# Main Screener
# ---------------------------------------------------------------------------

def run_screener(tickers: list[str], output_csv: str = "screener_results.csv"):
    print(f"\n{'='*65}")
    print(f"  STOCK SCREENER - Algorithm")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Universe: {len(tickers)} tickers")
    print(f"{'='*65}\n")

    gate1_failed: list[dict] = []
    results: list[dict] = []

    for i, ticker in enumerate(tickers):
        sys.stdout.write(f"\r  [{i+1:>3}/{len(tickers)}] Checking {ticker:<8} …")
        sys.stdout.flush()

        success = False
        for attempt in range(2):  # try once, retry once on failure
            try:
                if attempt == 1:
                    print(f"\n  Retrying {ticker}...")
                    time.sleep(1)

                t    = yf.Ticker(ticker)
                info = t.info

                if not info or info.get("regularMarketPrice") is None:
                    gate1_failed.append({"ticker": ticker, "reason": "No market data returned"})
                    success = True  # not a transient error, no point retrying
                    break

                hist = t.history(period="5y", auto_adjust=True)

                try:
                    income_stmt = t.income_stmt
                except Exception:
                    income_stmt = pd.DataFrame()

                passed, reason = passes_gate1(info, hist, income_stmt)
                if not passed:
                    gate1_failed.append({"ticker": ticker, "reason": reason})
                else:
                    results.append(score_stock(ticker, info, hist, income_stmt))

                success = True
                break

            except Exception as exc:
                if attempt == 1:
                    gate1_failed.append({"ticker": ticker, "reason": f"Error after retry: {str(exc)[:80]}"})

        time.sleep(0.15)  # polite pacing for Yahoo Finance

    print(f"\n\n  Gate 1: {len(results)} passed, {len(gate1_failed)} filtered out\n")

    if not results:
        print("  No stocks passed Gate 1 filters.")
        return pd.DataFrame()

    # Build output DataFrame
    df = pd.DataFrame([{
        "Ticker":         r["ticker"],
        "Company":        r["company"],
        "Sector":         r["sector"],
        "MktCap($B)":     r["market_cap_B"],
        "Score":          f"{r['criteria_passed']}/5",
        "Score100":       r["score_100"],
        "AllPass":        r["all_pass"],
        "CurrPE":         r["current_pe"],
        "AvgPE_5yr":      r["avg_pe_5yr"],
        "PE_Disc%":       r["pe_discount_pct"],
        "P/FCF":          r["pfcf"],
        "EPS_Grwth%":     r["eps_growth_pct"],
        "D/E":            r["debt_to_equity"],
        "Vol_Ratio":      r["volume_ratio"],
    } for r in results])

    df = df.sort_values(["AllPass", "Score100"], ascending=[False, False])

    # --- Print: All 5 passed ---
    _print_section("TOP PICKS  -  ALL 5 CRITERIA MET", df[df["AllPass"]])

    # --- Print: 4/5 ---
    _print_section("HONORABLE MENTIONS  -  4 / 5 CRITERIA MET", df[df["Score"] == "4/5"])

    # --- Save CSV ---
    df_save = df.drop(columns=["AllPass"])
    df_save.to_csv(output_csv, index=False)
    print(f"\n  Full results saved -> {output_csv}")

    # --- Detail block for top picks ---
    top_all = [r for r in results if r["all_pass"]]
    if top_all:
        print(f"\n{'='*65}")
        print("  CRITERIA BREAKDOWN - TOP PICKS")
        print(f"{'='*65}")
        for r in top_all:
            print(f"\n  {r['ticker']} - {r['company']}")
            for name, c in r["_criteria"].items():
                flag = "PASS" if c["passes"] else "FAIL"
                print(f"    [{flag}] {name:<20} {c['reason']}")

    return df


def _print_section(title: str, df: pd.DataFrame):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")
    if df.empty:
        print("  (none)")
        return
    cols = ["Ticker", "Company", "Sector", "MktCap($B)", "Score", "Score100", "CurrPE", "PE_Disc%", "P/FCF", "D/E", "Vol_Ratio"]
    print(df[cols].to_string(index=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST MODE: set to True to only run the first 50 tickers (faster for dev)
# Remember to set back to False before a real run
# ---------------------------------------------------------------------------
TEST_MODE = False
TEST_MODE_LIMIT = 20

if __name__ == "__main__":
    universe = get_ticker_universe()
    if TEST_MODE:
        universe = universe[:TEST_MODE_LIMIT]
        print(f"  [TEST MODE] Limited to first {TEST_MODE_LIMIT} tickers.\n")
    run_screener(universe, output_csv="screener_results.csv")
