# run.py  —  daily entry point
#
# Schedule this file on PythonAnywhere or Windows Task Scheduler.
# Each run:
#   1. Checks inbox for unsubscribe replies and updates blocklist
#   2. Fetches active subscriber list
#   3. Detects new subscribers and sends them the latest cached results
#   4. Runs the stock screener
#   5. Emails results to all active subscribers
#   6. Saves subscriber cache for next run

from datetime import date
from screener import get_ticker_universe, run_screener, is_trading_day
from subscribers import (
    get_subscribers,
    process_inbox_requests,
    load_cached_subscribers,
    save_subscriber_cache,
    find_new_subscribers,
)
from mailer import send_daily_results, send_welcome_email

# Set to True to limit to first 50 tickers for faster test runs
TEST_MODE       = False
TEST_MODE_LIMIT = 20


def main():
    print("=" * 65)
    print("  Stock Screener  -  Daily Run")
    print("=" * 65)

    # Step 0: Abort if today is not a trading day
    if not is_trading_day():
        print(f"\n  Today ({date.today()}) is not a trading day. Exiting.")
        return

    # Step 1: Process any subscribe/unsubscribe replies
    process_inbox_requests()

    # Step 2: Get active subscribers
    current_subs = get_subscribers()
    if not current_subs:
        print("No active subscribers found. Continuing with screener run anyway.")

    # Step 3: Welcome any new subscribers with latest cached results
    cached_subs = load_cached_subscribers()
    new_subs    = find_new_subscribers(current_subs, cached_subs)
    if new_subs:
        print(f"\nNew subscribers: {new_subs}")
        for email in new_subs:
            send_welcome_email(email)
    else:
        print("\nNo new subscribers since last run.")

    # Step 4: Run the screener
    universe = get_ticker_universe()
    if TEST_MODE:
        universe = universe[:TEST_MODE_LIMIT]
        print(f"\n[TEST MODE] Limited to first {TEST_MODE_LIMIT} tickers.\n")

    df = run_screener(universe, output_csv="screener_results.csv")

    # Step 5: Email results
    if df is not None and not df.empty and current_subs:
        send_daily_results(df, current_subs)
    elif not current_subs:
        print("\nNo subscribers to email.")
    else:
        print("\nScreener returned no results — email not sent.")

    # Step 6: Save subscriber cache
    save_subscriber_cache(current_subs)
    print("\nDone.")


if __name__ == "__main__":
    main()
