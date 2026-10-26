const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  istanbulDate,
  latestExpectedSession,
  isStale,
  relativeAge,
  formatIstanbul,
  describeQuote,
} = require(path.join(__dirname, '..', 'assets', 'freshness.js'));

// Istanbul is UTC+3 all year. October 2026: 19 Mon, 23 Fri, 24 Sat, 26 Mon, 28 Wed.
function istanbul(year, month, day, hour, minute = 0) {
  return new Date(Date.UTC(year, month - 1, day, hour - 3, minute));
}

function seconds(date) {
  return date.getTime() / 1000;
}

const fridayQuote = seconds(istanbul(2026, 10, 23, 18, 9));

test('istanbulDate uses the Istanbul calendar date', () => {
  assert.equal(istanbulDate(fridayQuote), '2026-10-23');
});

test('istanbulDate across Istanbul midnight on different UTC dates', () => {
  // 23:30 UTC on Thursday is already 02:30 Friday in Istanbul.
  assert.equal(istanbulDate(Date.UTC(2026, 9, 22, 23, 30) / 1000), '2026-10-23');
  // 20:59 UTC on Thursday is still 23:59 Thursday in Istanbul.
  assert.equal(istanbulDate(Date.UTC(2026, 9, 22, 20, 59) / 1000), '2026-10-22');
  // 21:00 UTC on Thursday is 00:00 Friday in Istanbul.
  assert.equal(istanbulDate(Date.UTC(2026, 9, 22, 21, 0) / 1000), '2026-10-23');
});

test('latestExpectedSession on weekdays after the open is today', () => {
  assert.equal(latestExpectedSession(istanbul(2026, 10, 26, 10, 0)), '2026-10-26');
  assert.equal(latestExpectedSession(istanbul(2026, 10, 28, 11, 0)), '2026-10-28');
  assert.equal(latestExpectedSession(istanbul(2026, 10, 23, 23, 59)), '2026-10-23');
});

test('latestExpectedSession before the open is the previous weekday', () => {
  assert.equal(latestExpectedSession(istanbul(2026, 10, 28, 9, 59)), '2026-10-27');
  assert.equal(latestExpectedSession(istanbul(2026, 10, 26, 9, 0)), '2026-10-23');
  assert.equal(latestExpectedSession(istanbul(2026, 10, 26, 0, 30)), '2026-10-23');
});

test('latestExpectedSession on the weekend is Friday', () => {
  assert.equal(latestExpectedSession(istanbul(2026, 10, 24, 12, 0)), '2026-10-23');
  assert.equal(latestExpectedSession(istanbul(2026, 10, 25, 23, 0)), '2026-10-23');
});

test('latestExpectedSession accepts epoch milliseconds', () => {
  assert.equal(latestExpectedSession(istanbul(2026, 10, 24, 12, 0).getTime()), '2026-10-23');
});

test('latestExpectedSession across Istanbul midnight on different UTC dates', () => {
  // Sunday 22:30 UTC is Monday 01:30 in Istanbul: before Monday's open, so Friday.
  assert.equal(latestExpectedSession(new Date(Date.UTC(2026, 9, 25, 22, 30))), '2026-10-23');
  // Friday 21:30 UTC is Saturday 00:30 in Istanbul: still Friday's session.
  assert.equal(latestExpectedSession(new Date(Date.UTC(2026, 9, 23, 21, 30))), '2026-10-23');
});

test('a Friday quote is fresh on Saturday and Sunday', () => {
  assert.equal(isStale(fridayQuote, istanbul(2026, 10, 24, 12, 0)), false);
  assert.equal(isStale(fridayQuote, istanbul(2026, 10, 25, 20, 0)), false);
});

test('a Friday quote is fresh on Monday before the first run', () => {
  assert.equal(isStale(fridayQuote, istanbul(2026, 10, 26, 9, 0)), false);
});

test('a Friday quote is fresh on Monday afternoon', () => {
  assert.equal(isStale(fridayQuote, istanbul(2026, 10, 26, 14, 0)), false);
});

test('the usual same-day and overnight lag is fresh', () => {
  const thursdayQuote = seconds(istanbul(2026, 10, 22, 18, 9));
  assert.equal(isStale(thursdayQuote, istanbul(2026, 10, 22, 20, 0)), false);
  assert.equal(isStale(thursdayQuote, istanbul(2026, 10, 23, 11, 0)), false);
  assert.equal(isStale(seconds(istanbul(2026, 10, 23, 10, 15)), istanbul(2026, 10, 23, 17, 0)), false);
});

test('a Monday quote is stale on Wednesday at 11:00', () => {
  const mondayQuote = seconds(istanbul(2026, 10, 19, 18, 9));
  assert.equal(isStale(mondayQuote, istanbul(2026, 10, 21, 11, 0)), true);
});

test('a Thursday quote is stale on Tuesday', () => {
  const thursdayQuote = seconds(istanbul(2026, 10, 22, 18, 9));
  assert.equal(isStale(thursdayQuote, istanbul(2026, 10, 27, 9, 0)), true);
  assert.equal(isStale(thursdayQuote, istanbul(2026, 10, 27, 14, 0)), true);
});

test('a missing or invalid market time is stale', () => {
  const now = istanbul(2026, 10, 26, 14, 0);
  assert.equal(isStale(undefined, now), true);
  assert.equal(isStale(null, now), true);
  assert.equal(isStale('1792768196', now), true);
  assert.equal(isStale(NaN, now), true);
});

test('isStale across Istanbul midnight on different UTC dates', () => {
  // Quote at Thursday 23:30 UTC = Friday 02:30 Istanbul: a Friday quote, fresh on Monday.
  const lateUtcThursday = Date.UTC(2026, 9, 22, 23, 30) / 1000;
  assert.equal(isStale(lateUtcThursday, istanbul(2026, 10, 26, 14, 0)), false);
  // Quote at Wednesday 20:30 UTC = Wednesday 23:30 Istanbul: a Wednesday quote, stale on Friday.
  const lateIstanbulWednesday = Date.UTC(2026, 9, 21, 20, 30) / 1000;
  assert.equal(isStale(lateIstanbulWednesday, istanbul(2026, 10, 23, 14, 0)), true);
});

test('relativeAge uses short English units', () => {
  const now = istanbul(2026, 10, 26, 14, 0);
  const ago = (ms) => (now.getTime() - ms) / 1000;
  assert.equal(relativeAge(ago(0), now), 'just now');
  assert.equal(relativeAge(ago(59 * 1000), now), 'just now');
  assert.equal(relativeAge(ago(60 * 1000), now), '1 min ago');
  assert.equal(relativeAge(ago(25 * 60 * 1000), now), '25 min ago');
  assert.equal(relativeAge(ago(59 * 60 * 1000), now), '59 min ago');
  assert.equal(relativeAge(ago(60 * 60 * 1000), now), '1 h ago');
  assert.equal(relativeAge(ago(3 * 3600 * 1000 + 59 * 60 * 1000), now), '3 h ago');
  assert.equal(relativeAge(ago(24 * 3600 * 1000), now), '1 day ago');
  assert.equal(relativeAge(ago(2 * 24 * 3600 * 1000 + 3600 * 1000), now), '2 days ago');
});

test('relativeAge treats future times as just now and rejects missing input', () => {
  const now = istanbul(2026, 10, 26, 14, 0);
  assert.equal(relativeAge(seconds(now) + 300, now), 'just now');
  assert.equal(relativeAge(undefined, now), '');
});

test('formatIstanbul shows the Istanbul date and 24-hour time', () => {
  assert.equal(formatIstanbul(fridayQuote, true), '23 Oct 2026, 18:09');
  assert.equal(formatIstanbul(fridayQuote), '23 Oct 2026');
  assert.equal(formatIstanbul(Date.UTC(2026, 9, 22, 21, 5) / 1000, true), '23 Oct 2026, 00:05');
});

test('describeQuote shows the market time and age of a fresh quote', () => {
  const quote = { marketTime: fridayQuote, updatedAt: '2026-10-23T17:28:45.159581+00:00' };
  assert.deepEqual(describeQuote(quote, istanbul(2026, 10, 25, 18, 9)), {
    stale: false,
    text: 'Quote from 23 Oct 2026, 18:09 Istanbul time · 2 days ago',
  });
});

test('describeQuote warns about a stale quote', () => {
  const quote = { marketTime: seconds(istanbul(2026, 10, 21, 18, 9)) };
  assert.deepEqual(describeQuote(quote, istanbul(2026, 10, 26, 14, 0)), {
    stale: true,
    text: '⚠ May be out of date: last quote from 21 Oct 2026, 18:09 Istanbul time · 4 days ago',
  });
});

test('describeQuote falls back to the fetch time when the market time is missing', () => {
  const quote = { updatedAt: '2026-10-23T17:28:45.159581+00:00' };
  const result = describeQuote(quote, istanbul(2026, 10, 23, 21, 0));
  assert.equal(result.stale, true);
  assert.equal(
    result.text,
    '⚠ May be out of date: quote time unknown · file fetched (not quoted) 23 Oct 2026, 20:28 Istanbul time · 31 min ago',
  );
  assert.doesNotMatch(result.text, /Quote from/);
});

test('describeQuote with no times at all', () => {
  assert.deepEqual(describeQuote({}, istanbul(2026, 10, 26, 14, 0)), {
    stale: true,
    text: '⚠ May be out of date: quote time unknown',
  });
});
