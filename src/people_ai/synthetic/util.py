"""Small date and sampling helpers shared by the simulation modules."""
import re
from datetime import timedelta

DAY = timedelta(days=1)


def days(n):
    return timedelta(days=n)


def next_month(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def month_end(d):
    return next_month(d) - DAY


def month_starts(start, end):
    d = start.replace(day=1)
    while d <= end:
        yield d
        d = next_month(d)


def rand_date(rng, lo, hi):
    return lo if hi <= lo else lo + timedelta(days=rng.randint(0, (hi - lo).days))


def monthly_prob(annual):
    return 1 - (1 - min(annual, 0.95)) ** (1 / 12)


def weighted(rng, mapping):
    return rng.choices(list(mapping), weights=list(mapping.values()))[0]


def email_slug(s):
    return re.sub(r"[^a-z]", "", s.lower())
