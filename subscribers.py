# subscribers.py  —  fetch subscriber list and manage unsubscribes
#
# To migrate off Google Sheets later: replace _fetch_from_google_sheet()
# with your own source and update get_subscribers() to call it. Nothing
# else in the codebase needs to change.

import imaplib
import json
import os
import urllib.request
import pandas as pd
from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GOOGLE_SHEET_CSV_URL

SUBSCRIBER_CACHE_FILE = "subscriber_cache.json"
UNSUBSCRIBE_FILE      = "unsubscribed.json"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_subscribers() -> list[str]:
    """Return active subscribers: Sheet list minus anyone who unsubscribed."""
    all_subs   = _fetch_from_google_sheet()
    unsub_list = _load_unsubscribed()
    active     = [e for e in all_subs if e not in unsub_list]
    if unsub_list:
        print(f"  ({len(unsub_list)} unsubscribed address(es) filtered out)")
    return active


def process_inbox_requests():
    """
    Check the Gmail inbox for:
      - Emails containing 'unsubscribe' -> add to blocklist
      - Emails containing 'subscribe'   -> remove from blocklist (resubscribe)
    Unsubscribe takes priority if both words appear in the same email.
    """
    unsub_list = _load_unsubscribed()
    changed    = False

    unsub_senders, resub_senders = _check_inbox_for_requests()

    unsub_set = set(unsub_list)

    for email in unsub_senders:
        if email not in unsub_set:
            unsub_set.add(email)
            print(f"Unsubscribed: {email}")
            changed = True

    for email in resub_senders:
        if email in unsub_set:
            unsub_set.discard(email)  # removes all occurrences, no error if missing
            print(f"Resubscribed: {email}")
            changed = True

    unsub_list = list(unsub_set)

    if not changed:
        print("No new subscribe/unsubscribe requests.")

    if changed:
        _save_unsubscribed(unsub_list)


def find_new_subscribers(current: list[str], cached: list[str]) -> list[str]:
    """Return emails in current that were not in the last cached run."""
    return [e for e in current if e not in set(cached)]


def load_cached_subscribers() -> list[str]:
    if not os.path.exists(SUBSCRIBER_CACHE_FILE):
        return []
    with open(SUBSCRIBER_CACHE_FILE) as f:
        return json.load(f)


def save_subscriber_cache(emails: list[str]):
    with open(SUBSCRIBER_CACHE_FILE, "w") as f:
        json.dump(emails, f)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_from_google_sheet() -> list[str]:
    try:
        req  = urllib.request.Request(GOOGLE_SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        df   = pd.read_csv(pd.io.common.StringIO(data))

        email_col = next((c for c in df.columns if "email" in c.lower()), df.columns[0])
        emails = df[email_col].dropna().str.strip().str.lower().unique().tolist()
        print(f"Fetched {len(emails)} subscriber(s) from Google Sheet.")
        return emails
    except Exception as e:
        print(f"WARNING: Could not fetch subscriber list: {e}")
        return []


def _check_inbox_for_requests() -> tuple[list[str], list[str]]:
    """
    Scan the inbox and return two lists:
      - unsub_senders: emailed with 'unsubscribe' in the body
      - resub_senders: emailed with 'subscribe' (but NOT 'unsubscribe') in the body
    Unsubscribe takes priority — if both words appear, it counts as an unsub.
    """
    import email as email_lib

    unsub_senders = []
    resub_senders = []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        _, unsub_data = mail.search(None, '(UNSEEN BODY "unsubscribe")')
        _, resub_data = mail.search(None, '(UNSEEN BODY "subscribe")')

        unsub_ids = set(unsub_data[0].split()) if unsub_data[0] else set()
        resub_ids = set(resub_data[0].split()) if resub_data[0] else set()

        # Pure resubscribe = has 'subscribe' but NOT 'unsubscribe'
        pure_resub_ids = resub_ids - unsub_ids

        def extract_sender(msg_id):
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            from_header = msg.get("From", "")
            if "<" in from_header:
                return from_header.split("<")[-1].strip(">").strip().lower()
            return from_header.strip().lower()

        for msg_id in unsub_ids:
            sender = extract_sender(msg_id)
            if sender and "@" in sender:
                unsub_senders.append(sender)
            mail.store(msg_id, "+FLAGS", "\\Seen")  # mark as read so it isn't processed again

        for msg_id in pure_resub_ids:
            sender = extract_sender(msg_id)
            if sender and "@" in sender:
                resub_senders.append(sender)
            mail.store(msg_id, "+FLAGS", "\\Seen")  # mark as read so it isn't processed again

        mail.logout()

    except Exception as e:
        print(f"WARNING: Could not check inbox: {e}")

    return unsub_senders, resub_senders


def _load_unsubscribed() -> list[str]:
    if not os.path.exists(UNSUBSCRIBE_FILE):
        return []
    with open(UNSUBSCRIBE_FILE) as f:
        return json.load(f)


def _save_unsubscribed(emails: list[str]):
    with open(UNSUBSCRIBE_FILE, "w") as f:
        json.dump(emails, f)
