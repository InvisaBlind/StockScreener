# run.py  —  weekly entry point (runs every Tuesday)
#
# Each run:
#   1. Checks inbox for unsubscribe/resubscribe replies
#   2. Fetches active subscriber list
#   3. Runs the stock screener
#   4. Emails results to all active subscribers

from datetime import date
from screener import get_ticker_universe, run_screener, is_trading_day
from subscribers import get_subscribers, process_inbox_requests
from mailer import send_weekly_results

# Set to True to limit to first 20 tickers for faster test runs
TEST_MODE       = False
TEST_MODE_LIMIT = 20


def main():
    print("=" * 65)
    print("  Stock Screener  -  Weekly Run")
    print("=" * 65)

    # Step 1: Abort if today is not a trading day
    if not is_trading_day():
        print(f"\n  Today ({date.today()}) is not a trading day. Exiting.")
        return

    # Step 2: Process any subscribe/unsubscribe replies
    process_inbox_requests()

    # Step 3: Get active subscribers
    current_subs = get_subscribers()
    if not current_subs:
        print("No active subscribers found. Continuing with screener run anyway.")

    # Step 4: Run the screener
    universe = get_ticker_universe()
    if TEST_MODE:
        universe = universe[:TEST_MODE_LIMIT]
        print(f"\n[TEST MODE] Limited to first {TEST_MODE_LIMIT} tickers.\n")

    df = run_screener(universe, output_csv="screener_results.csv")

    # Step 5: Email results
    if df is not None and not df.empty and current_subs:
        send_weekly_results(df, current_subs)
    elif not current_subs:
        print("\nNo subscribers to email.")
    else:
        print("\nScreener returned no results — email not sent.")

    print("\nDone.")


if __name__ == "__main__":
    main()
