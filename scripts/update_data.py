#!/usr/bin/env python3
"""Fetch BIST quotes from Yahoo Finance and write data/<key>.json files."""
import argparse
import json
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


def fetch_chart(quote):
    url = f'https://query2.finance.yahoo.com/v8/finance/chart/{quote}?interval=1d&range=5d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


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
        (ts, close)
        for ts, close in zip(timestamps, closes)
        if close is not None and local_date(ts, gmtoffset) < session
    ]
    if not earlier:
        raise RuntimeError(f'No close before the {session} session for {quote}')
    return max(earlier)[1]


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


def main(argv=None, fetch=fetch_chart):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR,
                        help='directory for the <key>.json files (default: %(default)s)')
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for file_key, stock in STOCKS.items():
        out = build_quote(stock, fetch(stock['quote']), datetime.now(timezone.utc))

        with open(args.out_dir / f'{file_key}.json', 'w') as f:
            json.dump(out, f, indent=2)

        print(f"{stock['symbol']}: {out['price']} TRY (prev close: {out['previousClose']})")


if __name__ == '__main__':
    main()
