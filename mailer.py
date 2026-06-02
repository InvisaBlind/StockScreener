# mailer.py  —  format and send the daily results email

import smtplib
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from zoneinfo import ZoneInfo
from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

ET = ZoneInfo("America/New_York")

LAST_RESULTS_FILE = "last_results.json"


# ---------------------------------------------------------------------------
# Results cache  —  lets new subscribers get the latest results immediately
# ---------------------------------------------------------------------------

def save_last_results(html: str):
    with open(LAST_RESULTS_FILE, "w") as f:
        json.dump({"html": html, "date": datetime.now(ET).strftime("%Y-%m-%d")}, f)


def load_last_results() -> dict | None:
    if not os.path.exists(LAST_RESULTS_FILE):
        return None
    with open(LAST_RESULTS_FILE) as f:
        data = json.load(f)
    if not data or "date" not in data or "html" not in data:
        return None
    return data


# ---------------------------------------------------------------------------
# Email formatting
# ---------------------------------------------------------------------------

def _df_to_html_table(subset) -> str:
    if subset.empty:
        return "<p><em>None today.</em></p>"

    cols = ["Ticker", "Company", "Sector", "MktCap($B)", "Score", "Score100",
            "CurrPE", "PE_Disc%", "P/FCF", "D/E", "Vol_Ratio"]

    headers = "".join(
        f"<th style='padding:6px 12px;text-align:left;background:#f0f0f0'>{c}</th>"
        for c in cols
    )
    rows = ""
    for _, r in subset[cols].iterrows():
        cells = "".join(
            f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>"
            f"{'-' if str(r[c]) == 'nan' else r[c]}</td>"
            for c in cols
        )
        rows += f"<tr>{cells}</tr>"

    return (
        f"<table style='border-collapse:collapse;font-size:13px;width:100%'>"
        f"<tr>{headers}</tr>{rows}</table>"
    )


def build_email_html(df, run_date: str) -> str:
    top  = df[df["AllPass"] == True]
    four = df[df["Score"] == "4/5"]

    unsubscribe_note = (
        "<p style='color:#aaa;font-size:11px;margin-top:32px'>"
        "To unsubscribe or resubscribe, send a new email to "
        "<a href='mailto:henryfitz.dev@gmail.com'>henryfitz.dev@gmail.com</a> "
        "with the word <strong>unsubscribe</strong> or <strong>subscribe</strong> in the body.</p>"
    )

    return f"""
    <html><body style='font-family:Arial,sans-serif;max-width:900px;margin:auto;padding:24px'>
        <h2 style='color:#1a1a1a'>Stock Screener &mdash; {run_date}</h2>
        <p style='color:#555'>Daily scan of the S&amp;P 500 for established, undervalued companies.</p>
        <hr style='border:none;border-top:1px solid #ddd'>

        <h3 style='color:#1a7a1a'>Top Picks &mdash; All 5 Criteria Met</h3>
        {_df_to_html_table(top)}

        <h3 style='color:#b87c00;margin-top:32px'>Honorable Mentions &mdash; 4 / 5 Criteria Met</h3>
        {_df_to_html_table(four)}

        {unsubscribe_note}
    </body></html>
    """


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _send(to_addresses: list[str], subject: str, html_body: str):
    if not to_addresses:
        print("No recipients — skipping send.")
        return

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_addresses)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to_addresses, msg.as_string())
    print(f"Email sent to {len(to_addresses)} recipient(s).")


def send_daily_results(df, recipients: list[str]):
    run_date  = datetime.now(ET).strftime("%B %d, %Y")
    html_body = build_email_html(df, run_date)
    save_last_results(html_body)
    _send(recipients, f"Stock Screener Results - {run_date}", html_body)


def send_welcome_email(new_subscriber: str):
    """Send a new subscriber the latest cached results immediately."""
    last = load_last_results()
    if last is None:
        print(f"No cached results yet — skipping welcome email for {new_subscriber}.")
        return

    welcome_banner = f"""
    <div style='background:#f7f7f7;padding:16px;border-left:4px solid #1a7a1a;margin-bottom:24px'>
        <strong>Welcome!</strong> You have been added to the Stock Screener list.
        Here are the most recent results from <strong>{last['date']}</strong>.
        You will receive fresh results every trading day.
    </div>
    """
    html_body = last["html"].replace("<hr", welcome_banner + "<hr", 1)
    _send([new_subscriber], f"Welcome! Stock Screener Results ({last['date']})", html_body)
    print(f"Welcome email sent to {new_subscriber} with results from {last['date']}.")
