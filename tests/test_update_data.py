import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
sys.path.insert(0, str(REPO_ROOT / 'scripts'))

import update_data  # noqa: E402

NOW = datetime(2026, 10, 20, 17, 33, 19, 328167, tzinfo=timezone.utc)
TEST_STOCK = {'symbol': 'TEST', 'quote': 'TEST.IS'}

# Previous-session closes read off each recorded response's daily bars
# (Monday 2026-10-19 for a Tuesday 2026-10-20 quote).
REAL_PREVIOUS_CLOSES = {
    'ttkom': 50.05,
    'tursg': 4.71,
    'vakbn': 32.54,
    'halkb': 41.76,
    'tcell': 96.2,
}

# The data-file schema that index.html reads; None marks optional values.
# Prices may be ints: Yahoo sends whole numbers as JSON integers and round()
# keeps them that way.
NUMBER = (int, float)
SCHEMA = {
    'symbol': (str,),
    'exchange': (str,),
    'currency': (str,),
    'price': NUMBER,
    'previousClose': NUMBER,
    'dayHigh': NUMBER + (type(None),),
    'dayLow': NUMBER + (type(None),),
    'volume': (int, type(None)),
    'updatedAt': (str,),
    'marketTime': (int, type(None)),
}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def real_fixture(key):
    return load(f'real_{key}_2026-10-20.json')


def assert_matches_schema(test, data):
    test.assertEqual(list(data), list(SCHEMA))
    for key, types in SCHEMA.items():
        test.assertIsInstance(data[key], types, key)


class RealResponseTests(unittest.TestCase):
    def test_previous_close_is_the_previous_session_close(self):
        for key, expected in REAL_PREVIOUS_CLOSES.items():
            with self.subTest(key):
                chart = real_fixture(key)
                meta = chart['chart']['result'][0]['meta']
                out = update_data.build_quote(update_data.STOCKS[key], chart, NOW)
                self.assertEqual(out['previousClose'], expected)
                self.assertNotEqual(out['previousClose'], round(meta['chartPreviousClose'], 2))

    def test_change_percent_matches_yahoo(self):
        for key in REAL_PREVIOUS_CLOSES:
            with self.subTest(key):
                chart = real_fixture(key)
                meta = chart['chart']['result'][0]['meta']
                out = update_data.build_quote(update_data.STOCKS[key], chart, NOW)
                pct = (out['price'] - out['previousClose']) / out['previousClose'] * 100
                self.assertAlmostEqual(pct, meta['regularMarketChangePercent'], delta=0.05)

    def test_output_matches_data_file_schema(self):
        out = update_data.build_quote(update_data.STOCKS['ttkom'], real_fixture('ttkom'), NOW)
        assert_matches_schema(self, out)
        for key in ('price', 'previousClose', 'dayHigh', 'dayLow'):
            self.assertEqual(out[key], round(out[key], 2), key)
        self.assertEqual(out, {
            'symbol': 'TTKOM',
            'exchange': 'BIST',
            'currency': 'TRY',
            'price': 51.2,
            'previousClose': 50.05,
            'dayHigh': 51.5,
            'dayLow': 49.72,
            'volume': 18091550,
            'updatedAt': '2026-10-20T17:33:19.328167+00:00',
            'marketTime': 1792508981,
        })

    def test_committed_data_files_use_the_same_schema(self):
        for path in sorted((REPO_ROOT / 'data').glob('*.json')):
            with self.subTest(path.name):
                assert_matches_schema(self, json.loads(path.read_text()))

    def test_whole_number_prices_round_trip_through_the_schema(self):
        chart = real_fixture('ttkom')
        chart['chart']['result'][0]['meta'].update(
            regularMarketPrice=51, regularMarketDayHigh=52, regularMarketDayLow=50)
        out = update_data.build_quote(update_data.STOCKS['ttkom'], chart, NOW)
        assert_matches_schema(self, json.loads(json.dumps(out)))


class SessionEdgeCaseTests(unittest.TestCase):
    def previous_close(self, chart):
        return update_data.build_quote(TEST_STOCK, chart, NOW)['previousClose']

    def test_monday_compares_against_friday(self):
        self.assertEqual(self.previous_close(load('synthetic_weekend_monday.json')), 50.9)

    def test_holiday_gap_compares_against_last_open_session(self):
        self.assertEqual(self.previous_close(load('synthetic_holiday_gap.json')), 51.6)

    def test_null_close_on_latest_bar(self):
        self.assertEqual(self.previous_close(load('synthetic_null_latest_close.json')), 50.05)

    def test_missing_previous_session_close_rejects_quote(self):
        chart = load('synthetic_null_latest_close.json')
        closes = chart['chart']['result'][0]['indicators']['quote'][0]['close']
        for missing in (closes[:2] + [None, None], closes[:2], []):
            with self.subTest(closes=missing):
                chart['chart']['result'][0]['indicators']['quote'][0]['close'] = missing
                with self.assertRaisesRegex(
                        RuntimeError, 'No close for the 2026-10-19 previous session for TEST.IS'):
                    self.previous_close(chart)

    def test_pre_open_compares_quote_session_against_the_one_before(self):
        self.assertEqual(self.previous_close(load('synthetic_pre_open.json')), 50.05)

    def test_pre_open_ignores_a_close_on_the_new_sessions_bar(self):
        chart = load('synthetic_pre_open.json')
        chart['chart']['result'][0]['indicators']['quote'][0]['close'][-1] = 51.2
        self.assertEqual(self.previous_close(chart), 50.05)

    def test_no_earlier_close_raises(self):
        with self.assertRaisesRegex(RuntimeError, 'No close for the 2026-10-16 previous session for TEST.IS'):
            update_data.build_quote(TEST_STOCK, load('synthetic_no_earlier_close.json'), NOW)

    def test_no_earlier_session_raises(self):
        chart = load('synthetic_no_earlier_close.json')
        result = chart['chart']['result'][0]
        result['timestamp'] = result['timestamp'][1:]
        result['indicators']['quote'][0]['close'] = [50.05]
        with self.assertRaisesRegex(RuntimeError, 'No close before the 2026-10-19 session for TEST.IS'):
            self.previous_close(chart)

    def test_missing_chart_result_raises(self):
        with self.assertRaisesRegex(RuntimeError, 'No Yahoo Finance chart result for TEST.IS'):
            update_data.build_quote(TEST_STOCK, {'chart': {'result': None, 'error': {}}}, NOW)

    def test_missing_optional_meta_values_are_null(self):
        chart = load('synthetic_weekend_monday.json')
        meta = chart['chart']['result'][0]['meta']
        for key in ('regularMarketDayHigh', 'regularMarketDayLow', 'regularMarketVolume'):
            del meta[key]
        out = update_data.build_quote(TEST_STOCK, chart, NOW)
        self.assertIsNone(out['dayHigh'])
        self.assertIsNone(out['dayLow'])
        self.assertIsNone(out['volume'])


HTTP_ERRORS = []


def http_error(code):
    error = urllib.error.HTTPError('https://example.invalid', code, 'error', {}, io.BytesIO())
    HTTP_ERRORS.append(error)
    return error


def tearDownModule():
    for error in HTTP_ERRORS:
        error.close()


def no_sleep(seconds):
    pass


def fetch_real(quote):
    return real_fixture(quote.split('.')[0].lower())


def valid_quote(**changes):
    out = update_data.build_quote(update_data.STOCKS['ttkom'], real_fixture('ttkom'), NOW)
    out.update(changes)
    return out


class RetryTests(unittest.TestCase):
    def fetch_failing(self, errors):
        """A fetch that raises each of errors in turn, then succeeds."""
        calls = []

        def fetch(quote):
            calls.append(quote)
            if len(calls) <= len(errors):
                raise errors[len(calls) - 1]
            return fetch_real(quote)
        return fetch, calls

    def fetch_with_retries(self, fetch, sleeps):
        with contextlib.redirect_stdout(io.StringIO()):
            return update_data.fetch_with_retries('TTKOM.IS', fetch=fetch, sleep=sleeps.append)

    def test_transient_errors_are_retried_with_exponential_backoff(self):
        errors = [urllib.error.URLError('connection refused'), TimeoutError('timed out'),
                  http_error(503)]
        fetch, calls = self.fetch_failing(errors)
        sleeps = []
        self.assertEqual(self.fetch_with_retries(fetch, sleeps), real_fixture('ttkom'))
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [2, 4, 8])

    def test_each_transient_error_is_retried(self):
        for error in (http_error(429), http_error(500), http_error(502),
                      json.JSONDecodeError('Expecting value', '', 0),
                      ConnectionResetError('reset')):
            with self.subTest(error=error):
                fetch, calls = self.fetch_failing([error])
                sleeps = []
                self.assertEqual(self.fetch_with_retries(fetch, sleeps), real_fixture('ttkom'))
                self.assertEqual(len(calls), 2)
                self.assertEqual(sleeps, [2])

    def test_gives_up_after_the_attempt_limit(self):
        fetch, calls = self.fetch_failing([http_error(503)] * 10)
        sleeps = []
        with self.assertRaises(urllib.error.HTTPError):
            self.fetch_with_retries(fetch, sleeps)
        self.assertEqual(len(calls), update_data.FETCH_ATTEMPTS)
        self.assertEqual(sleeps, [2, 4, 8])

    def test_permanent_errors_are_not_retried(self):
        for error in (http_error(400), http_error(403), http_error(404), RuntimeError('bad')):
            with self.subTest(error=error):
                fetch, calls = self.fetch_failing([error])
                sleeps = []
                with self.assertRaises(type(error)):
                    self.fetch_with_retries(fetch, sleeps)
                self.assertEqual(len(calls), 1)
                self.assertEqual(sleeps, [])


class ValidateQuoteTests(unittest.TestCase):
    def assert_rejected(self, out, message, existing=None):
        with self.assertRaisesRegex(ValueError, message):
            update_data.validate_quote(out, NOW, existing)

    def test_accepts_all_recorded_real_responses(self):
        for key in REAL_PREVIOUS_CLOSES:
            with self.subTest(key):
                out = update_data.build_quote(update_data.STOCKS[key], real_fixture(key), NOW)
                update_data.validate_quote(out, NOW)

    def test_accepts_missing_optional_values(self):
        update_data.validate_quote(valid_quote(dayHigh=None, dayLow=None, volume=None), NOW)
        update_data.validate_quote(valid_quote(dayHigh=None), NOW)
        update_data.validate_quote(valid_quote(volume=0), NOW)

    def test_rejects_a_price_that_is_not_finite_and_positive(self):
        for price in (None, 0, -1.5, float('nan'), float('inf'), True, '51.2'):
            with self.subTest(price=price):
                self.assert_rejected(valid_quote(price=price), 'price is not a positive number')

    def test_rejects_a_previous_close_that_is_not_finite_and_positive(self):
        for close in (None, 0, -1.5, float('nan'), float('inf')):
            with self.subTest(close=close):
                self.assert_rejected(valid_quote(previousClose=close),
                                     'previousClose is not a positive number')

    def test_rejects_a_currency_other_than_try(self):
        for currency in ('USD', None, 'try'):
            with self.subTest(currency=currency):
                self.assert_rejected(valid_quote(currency=currency), 'currency is not TRY')

    def test_rejects_a_market_time_that_is_not_an_int(self):
        for market_time in (None, 1792508981.5, '1792508981', True):
            with self.subTest(market_time=market_time):
                self.assert_rejected(valid_quote(marketTime=market_time),
                                     'marketTime is not an integer')

    def test_rejects_a_market_time_in_the_future(self):
        allowed = int((NOW + update_data.MAX_FUTURE_SKEW).timestamp())
        update_data.validate_quote(valid_quote(marketTime=allowed), NOW)
        self.assert_rejected(valid_quote(marketTime=allowed + 1), 'is in the future')

    def test_rejects_day_low_above_day_high(self):
        update_data.validate_quote(valid_quote(dayLow=50, dayHigh=50), NOW)
        self.assert_rejected(valid_quote(dayLow=51.6, dayHigh=51.5), 'dayLow 51.6 is above dayHigh 51.5')

    def test_rejects_a_volume_that_is_not_a_non_negative_int(self):
        for volume in (-1, 1.5, '100', True):
            with self.subTest(volume=volume):
                self.assert_rejected(valid_quote(volume=volume),
                                     'volume is not a non-negative integer')

    def test_rejects_a_quote_older_than_the_existing_file(self):
        out = valid_quote()
        update_data.validate_quote(out, NOW, {'marketTime': out['marketTime']})
        update_data.validate_quote(out, NOW, {'marketTime': out['marketTime'] - 1})
        self.assert_rejected(out, 'is older than the existing file',
                             existing={'marketTime': out['marketTime'] + 1})


class WriteAtomicallyTests(unittest.TestCase):
    def test_replaces_the_file_through_a_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ttkom.json'
            path.write_text('old')
            replaced = []
            real_replace = update_data.os.replace

            def replace(src, dst):
                replaced.append((Path(src).parent, Path(dst)))
                real_replace(src, dst)

            with mock.patch.object(update_data.os, 'replace', replace):
                update_data.write_atomically(path, {'price': 1})
            self.assertEqual(replaced, [(Path(tmp), path)])
            self.assertEqual(path.read_text(), json.dumps({'price': 1}, indent=2))
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_failed_write_leaves_the_file_and_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ttkom.json'
            path.write_text('old')
            with self.assertRaises(TypeError):
                update_data.write_atomically(path, {'price': object()})
            self.assertEqual(path.read_text(), 'old')
            self.assertEqual(list(Path(tmp).iterdir()), [path])


class MainTests(unittest.TestCase):
    def run_main(self, out_dir, fetch, sleep=no_sleep):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            update_data.main(['--out-dir', str(out_dir)], fetch=fetch, sleep=sleep)
        return stdout.getvalue()

    def test_writes_one_file_per_ticker(self):
        fetched = []

        def fetch(quote):
            fetched.append(quote)
            return real_fixture(quote.split('.')[0].lower())

        with tempfile.TemporaryDirectory() as tmp:
            self.run_main(tmp, fetch)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             sorted(f'{key}.json' for key in update_data.STOCKS))
            for key, expected in REAL_PREVIOUS_CLOSES.items():
                data = json.loads((Path(tmp) / f'{key}.json').read_text())
                self.assertEqual(data['symbol'], update_data.STOCKS[key]['symbol'])
                self.assertEqual(data['previousClose'], expected)
        self.assertEqual(fetched, [s['quote'] for s in update_data.STOCKS.values()])

    def assert_isolated_failure(self, fetch, reason, sleep=no_sleep):
        """VAKBN fails; the rest are written and vakbn.json is left as it was."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'vakbn.json'
            original = (REPO_ROOT / 'data' / 'vakbn.json').read_bytes()
            path.write_bytes(original)
            output = self.run_main(tmp, fetch, sleep)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             sorted(f'{key}.json' for key in update_data.STOCKS))
            for key in REAL_PREVIOUS_CLOSES.keys() - {'vakbn'}:
                data = json.loads((Path(tmp) / f'{key}.json').read_text())
                self.assertEqual(data['previousClose'], REAL_PREVIOUS_CLOSES[key])
        warnings = [line for line in output.splitlines() if line.startswith('::warning::')]
        self.assertEqual(len(warnings), 1)
        self.assertIn('VAKBN', warnings[0])
        self.assertIn(reason, warnings[0])

    def test_bad_response_keeps_that_tickers_file_and_writes_the_rest(self):
        def fetch(quote):
            if quote == 'VAKBN.IS':
                return load('synthetic_no_earlier_close.json')
            return fetch_real(quote)

        self.assert_isolated_failure(fetch, 'No close for the 2026-10-16 previous session')

    def test_fetch_failing_after_retries_keeps_that_tickers_file_and_writes_the_rest(self):
        sleeps = []

        def fetch(quote):
            if quote == 'VAKBN.IS':
                raise http_error(503)
            return fetch_real(quote)

        self.assert_isolated_failure(fetch, 'HTTP Error 503', sleep=sleeps.append)
        self.assertEqual(sleeps, [2, 4, 8])

    def test_invalid_quote_keeps_that_tickers_file_and_writes_the_rest(self):
        def fetch(quote):
            chart = fetch_real(quote)
            if quote == 'VAKBN.IS':
                chart['chart']['result'][0]['meta']['currency'] = 'USD'
            return chart

        self.assert_isolated_failure(fetch, "currency is not TRY: 'USD'")

    def test_retried_fetch_writes_a_normal_file(self):
        attempts = []

        def fetch(quote):
            attempts.append(quote)
            if attempts.count(quote) == 1:
                raise urllib.error.URLError('temporary failure')
            return fetch_real(quote)

        sleeps = []
        with tempfile.TemporaryDirectory() as tmp:
            output = self.run_main(tmp, fetch, sleep=sleeps.append)
            for key, expected in REAL_PREVIOUS_CLOSES.items():
                data = json.loads((Path(tmp) / f'{key}.json').read_text())
                assert_matches_schema(self, data)
                self.assertEqual(data['previousClose'], expected)
        self.assertEqual(sleeps, [2] * len(update_data.STOCKS))
        self.assertNotIn('::warning::', output)

    def test_every_ticker_failing_raises_and_writes_nothing(self):
        sleeps = []

        def fetch(quote):
            raise urllib.error.URLError('network is down')

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'Every ticker failed'):
                self.run_main(tmp, fetch, sleep=sleeps.append)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(sleeps, [2, 4, 8] * len(update_data.STOCKS))

    def test_quote_older_than_the_existing_file_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ttkom.json'
            # The committed file is from 2026-10-23, newer than the 2026-10-20 fixture.
            original = (REPO_ROOT / 'data' / 'ttkom.json').read_bytes()
            path.write_bytes(original)
            output = self.run_main(tmp, fetch_real)
            self.assertEqual(path.read_bytes(), original)
        self.assertIn('::warning::TTKOM', output)
        self.assertIn('older than the existing file', output)

    def test_missing_previous_close_preserves_existing_quote_file(self):
        chart = real_fixture('ttkom')
        chart['chart']['result'][0]['indicators']['quote'][0]['close'][-2] = None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ttkom.json'
            original = (REPO_ROOT / 'data' / 'ttkom.json').read_text()
            path.write_text(original)
            with self.assertRaisesRegex(RuntimeError, 'No close for the 2026-10-19 previous session'):
                self.run_main(tmp, lambda quote: chart)
            self.assertEqual(path.read_text(), original)

    def test_default_out_dir_is_repo_data_dir(self):
        self.assertEqual(update_data.DEFAULT_OUT_DIR, REPO_ROOT / 'data')


if __name__ == '__main__':
    unittest.main()
