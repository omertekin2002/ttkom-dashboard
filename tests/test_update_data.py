import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


class MainTests(unittest.TestCase):
    def run_main(self, out_dir, fetch):
        with contextlib.redirect_stdout(io.StringIO()):
            update_data.main(['--out-dir', str(out_dir)], fetch=fetch)

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

    def test_bad_response_raises_and_writes_nothing_for_that_ticker(self):
        def fetch(quote):
            if quote == 'VAKBN.IS':
                return load('synthetic_no_earlier_close.json')
            return real_fixture(quote.split('.')[0].lower())

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.run_main(tmp, fetch)
            self.assertFalse((Path(tmp) / 'vakbn.json').exists())

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
