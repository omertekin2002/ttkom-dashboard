#!/usr/bin/env python3
"""Fetch BIST quotes from Yahoo Finance and write data/<key>.json files."""
import argparse
import contextlib
import http.client
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STOCKS = {
    'ttkom': {'symbol': 'TTKOM', 'quote': 'TTKOM.IS'},
    'tursg': {'symbol': 'TURSG', 'quote': 'TURSG.IS'},
    'vakbn': {'symbol': 'VAKBN', 'quote': 'VAKBN.IS'},
    'halkb': {'symbol': 'HALKB', 'quote': 'HALKB.IS'},
    'tcell': {'symbol': 'TCELL', 'quote': 'TCELL.IS'},
}

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / 'data'

FETCH_ATTEMPTS = 4
RETRY_BASE_DELAY = 2  # seconds; doubles after each failed attempt
MAX_FUTURE_SKEW = timedelta(minutes=10)


def fetch_chart(quote):
    url = f'https://query2.finance.yahoo.com/v8/finance/chart/{quote}?interval=1d&range=5d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def is_transient(error):
    """Whether a fetch error is worth retrying.

    HTTP 429 and 5xx, network errors, timeouts and unparseable responses are
    retried. Other HTTP errors, such as a 404 for an unknown symbol, are not.
    """
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, (OSError, http.client.HTTPException, json.JSONDecodeError))


def fetch_with_retries(quote, fetch=fetch_chart, sleep=time.sleep,
                       attempts=FETCH_ATTEMPTS, base_delay=RETRY_BASE_DELAY):
    for attempt in range(attempts):
        try:
            return fetch(quote)
        except Exception as error:
            if attempt == attempts - 1 or not is_transient(error):
                raise
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            delay = base_delay * 2 ** attempt
            print(f'{quote}: attempt {attempt + 1} failed ({error!r}), retrying in {delay}s')
            sleep(delay)


def chart_result(chart_json, quote):
    result = (chart_json.get('chart') or {}).get('result')
    if not result:
        raise RuntimeError(f'No Yahoo Finance chart result for {quote}')
    return result[0]


def local_date(timestamp, gmtoffset):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(seconds=gmtoffset))).date()


def previous_session_close(result, quote):
    """Close of the last session before the session of regularMarketTime.

    Daily bars are compared by exchange-local date, so weekends, holidays,
    the pre-open period and a null close on the latest bar are all handled.
    A missing close on the previous session rejects the quote rather than
    silently using an older session's close.
    chartPreviousClose is not used: with range=5d it is the close from
    before the whole window, not the previous session's.
    """
    meta = result['meta']
    market_time = meta.get('regularMarketTime')
    if market_time is None:
        raise RuntimeError(f'No regularMarketTime for {quote}')
    gmtoffset = meta.get('gmtoffset') or 0
    session = local_date(market_time, gmtoffset)

    timestamps = result.get('timestamp') or []
    quotes = (result.get('indicators') or {}).get('quote') or [{}]
    closes = quotes[0].get('close') or []

    earlier = [
        (ts, index)
        for index, ts in enumerate(timestamps)
        if local_date(ts, gmtoffset) < session
    ]
    if not earlier:
        raise RuntimeError(f'No close before the {session} session for {quote}')
    previous_timestamp, index = max(earlier)
    if index >= len(closes) or closes[index] is None:
        previous_session = local_date(previous_timestamp, gmtoffset)
        raise RuntimeError(f'No close for the {previous_session} previous session for {quote}')
    return closes[index]


def rounded(meta, key):
    value = meta.get(key)
    return round(value, 2) if value is not None else None


def build_quote(stock, chart_json, now):
    result = chart_result(chart_json, stock['quote'])
    meta = result['meta']
    previous_close = previous_session_close(result, stock['quote'])

    return {
        'symbol': stock['symbol'],
        'exchange': 'BIST',
        'currency': meta['currency'],
        'price': round(meta['regularMarketPrice'], 2),
        'previousClose': round(previous_close, 2),
        'dayHigh': rounded(meta, 'regularMarketDayHigh'),
        'dayLow': rounded(meta, 'regularMarketDayLow'),
        'volume': meta.get('regularMarketVolume'),
        'updatedAt': now.isoformat(),
        'marketTime': meta.get('regularMarketTime'),
    }


def is_positive_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_quote(out, now, existing=None):
    """Raise ValueError unless out is a sane quote to write.

    existing is the quote already on disk, if any: a quote whose marketTime
    is older than it is rejected so the data never moves backwards in time.
    """
    for key in ('price', 'previousClose'):
        if not is_positive_number(out.get(key)):
            raise ValueError(f'{key} is not a positive number: {out.get(key)!r}')
    if out.get('currency') != 'TRY':
        raise ValueError(f"currency is not TRY: {out.get('currency')!r}")

    market_time = out.get('marketTime')
    if not is_int(market_time):
        raise ValueError(f'marketTime is not an integer: {market_time!r}')
    if market_time > (now + MAX_FUTURE_SKEW).timestamp():
        raise ValueError(f'marketTime {market_time} is in the future')

    low, high = out.get('dayLow'), out.get('dayHigh')
    if low is not None and high is not None and low > high:
        raise ValueError(f'dayLow {low} is above dayHigh {high}')

    volume = out.get('volume')
    if volume is not None and not (is_int(volume) and volume >= 0):
        raise ValueError(f'volume is not a non-negative integer: {volume!r}')

    existing_time = (existing or {}).get('marketTime')
    if is_int(existing_time) and existing_time > market_time:
        raise ValueError(
            f'marketTime {market_time} is older than the existing file\'s {existing_time}')


def read_existing(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None  # an unreadable file is replaced by a good quote
    return data if isinstance(data, dict) else None


def write_atomically(path, out):
    """Write out as JSON to path via a temp file and os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(out, f, indent=2)
        os.chmod(tmp, 0o644)  # mkstemp creates the file owner-only
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def warning(message):
    """Print a GitHub Actions warning annotation."""
    escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::warning::{escaped}')


def update_stock(file_key, stock, out_dir, fetch, sleep):
    chart = fetch_with_retries(stock['quote'], fetch=fetch, sleep=sleep)
    now = datetime.now(timezone.utc)
    out = build_quote(stock, chart, now)
    path = out_dir / f'{file_key}.json'
    validate_quote(out, now, read_existing(path))
    write_atomically(path, out)
    return out


def main(argv=None, fetch=fetch_chart, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR,
                        help='directory for the <key>.json files (default: %(default)s)')
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    for file_key, stock in STOCKS.items():
        try:
            out = update_stock(file_key, stock, args.out_dir, fetch, sleep)
        except Exception as error:
            failures.append(f"{stock['symbol']}: {error}")
            warning(f"{stock['symbol']} not updated, keeping its last good file: {error}")
            continue
        print(f"{stock['symbol']}: {out['price']} TRY (prev close: {out['previousClose']})")

    if len(failures) == len(STOCKS):
        raise RuntimeError('Every ticker failed: ' + '; '.join(failures))


if __name__ == '__main__':
    main()
