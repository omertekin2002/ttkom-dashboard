// Freshness helpers for the market board. Dependency-free classic script:
// the page reads them from window.Freshness, Node tests through module.exports.
(function (root) {
  'use strict';

  const TIME_ZONE = 'Europe/Istanbul';
  const SESSION_OPEN_MINUTES = 10 * 60; // BIST opens 10:00 Istanbul time
  const DAY_MS = 24 * 60 * 60 * 1000;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  const partsFormatter = new Intl.DateTimeFormat('en-US', {
    timeZone: TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });

  function toMs(value) {
    return value instanceof Date ? value.getTime() : Number(value);
  }

  // Calendar fields of an instant (epoch milliseconds) in Istanbul.
  function istanbulParts(ms) {
    const parts = {};
    partsFormatter.formatToParts(new Date(ms)).forEach((p) => {
      if (p.type !== 'literal') parts[p.type] = Number(p.value);
    });
    return parts;
  }

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function dateKey(year, month, day) {
    return year + '-' + pad(month) + '-' + pad(day);
  }

  // Day of week (0 = Sunday) and day arithmetic on plain YYYY-MM-DD dates.
  function keyToUtcMs(key) {
    const [y, m, d] = key.split('-').map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function utcMsToKey(ms) {
    const d = new Date(ms);
    return dateKey(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
  }

  function isWeekday(key) {
    const dow = new Date(keyToUtcMs(key)).getUTCDay();
    return dow >= 1 && dow <= 5;
  }

  // Istanbul calendar date (YYYY-MM-DD) of a Unix timestamp in seconds.
  function istanbulDate(epochSeconds) {
    const p = istanbulParts(epochSeconds * 1000);
    return dateKey(p.year, p.month, p.day);
  }

  // Most recent Mon–Fri Istanbul date whose session has opened as of `now`.
  function latestExpectedSession(now) {
    const p = istanbulParts(toMs(now));
    let key = dateKey(p.year, p.month, p.day);
    if (isWeekday(key) && p.hour * 60 + p.minute >= SESSION_OPEN_MINUTES) return key;
    do {
      key = utcMsToKey(keyToUtcMs(key) - DAY_MS);
    } while (!isWeekday(key));
    return key;
  }

  // Weekdays in (fromKey, toKey]; 0 when fromKey is on or after toKey.
  function weekdaysBetween(fromKey, toKey) {
    let count = 0;
    for (let ms = keyToUtcMs(fromKey) + DAY_MS; ms <= keyToUtcMs(toKey); ms += DAY_MS) {
      if (isWeekday(utcMsToKey(ms))) count += 1;
    }
    return count;
  }

  // True when the quote's session is more than one weekday behind the latest
  // expected session. A missing market time counts as stale.
  function isStale(marketTime, now) {
    if (!Number.isFinite(marketTime)) return true;
    return weekdaysBetween(istanbulDate(marketTime), latestExpectedSession(now)) > 1;
  }

  // Short English age such as "just now", "25 min ago", "3 h ago", "2 days ago".
  function relativeAge(epochSeconds, now) {
    if (!Number.isFinite(epochSeconds)) return '';
    const minutes = Math.floor((toMs(now) - epochSeconds * 1000) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return minutes + ' min ago';
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + ' h ago';
    const days = Math.floor(hours / 24);
    return days === 1 ? '1 day ago' : days + ' days ago';
  }

  // "23 Oct 2026" or, with time, "23 Oct 2026, 18:09", in Istanbul time.
  function formatIstanbul(epochSeconds, withTime) {
    const p = istanbulParts(epochSeconds * 1000);
    const date = p.day + ' ' + MONTHS[p.month - 1] + ' ' + p.year;
    return withTime ? date + ', ' + pad(p.hour) + ':' + pad(p.minute) : date;
  }

  // Meta line for a quote file: the quote's market time and age, falling back
  // to the file's fetch time (updatedAt) when the market time is missing.
  function describeQuote(quote, now) {
    const marketTime = quote && quote.marketTime;
    const stale = isStale(marketTime, now);
    if (Number.isFinite(marketTime)) {
      const when = formatIstanbul(marketTime, true) + ' Istanbul time · ' + relativeAge(marketTime, now);
      return {
        stale,
        text: stale ? '⚠ May be out of date: last quote from ' + when : 'Quote from ' + when,
      };
    }
    const fetchedMs = quote && quote.updatedAt ? Date.parse(quote.updatedAt) : NaN;
    if (Number.isFinite(fetchedMs)) {
      const fetched = fetchedMs / 1000;
      return {
        stale,
        text: '⚠ May be out of date: quote time unknown · file fetched (not quoted) '
          + formatIstanbul(fetched, true) + ' Istanbul time · ' + relativeAge(fetched, now),
      };
    }
    return { stale, text: '⚠ May be out of date: quote time unknown' };
  }

  const api = { istanbulDate, latestExpectedSession, isStale, relativeAge, formatIstanbul, describeQuote };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.Freshness = api;
})(typeof window !== 'undefined' ? window : null);
