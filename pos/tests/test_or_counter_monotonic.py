"""FLAG-057 — OR numbering is installation-wide monotonic.

BIR Form 1900 / CSET evaluation requires the official-receipt sequence to be
strictly increasing across the lifetime of the installation. The NNNN suffix
must NOT reset to 0001 when a new calendar day (PHT) begins.
"""
from datetime import datetime, timezone as dt_tz, timedelta
from unittest.mock import patch

from django.test import TestCase

from pos.models import OfficialReceiptCounter, PosTransaction

PHT = dt_tz(timedelta(hours=8))


def _nnnn(or_number):
    """Extract the integer NNNN suffix from an OR-YYYYMMDD-NNNN string."""
    return int(or_number.rsplit('-', 1)[1])


def _date_segment(or_number):
    return or_number.split('-')[1]


class OrCounterMonotonicTests(TestCase):
    def _generate_on(self, year, month, day):
        """Generate an OR number as if the PHT wall-clock date were given."""
        fake_now = datetime(year, month, day, 10, 0, tzinfo=PHT)
        with patch('django.utils.timezone.now', return_value=fake_now):
            return PosTransaction._generate_or_number()

    def test_counter_does_not_reset_across_days(self):
        # Two receipts on day one.
        or1 = self._generate_on(2026, 6, 1)
        or2 = self._generate_on(2026, 6, 1)
        # One receipt on the *next* day.
        or3 = self._generate_on(2026, 6, 2)

        self.assertEqual(_nnnn(or1), 1)
        self.assertEqual(_nnnn(or2), 2)
        # FLAG-057: the new day must continue the sequence, not reset to 0001.
        self.assertGreater(_nnnn(or3), _nnnn(or2))
        self.assertEqual(_nnnn(or3), 3)

        # Date segments still roll over for human readability.
        self.assertEqual(_date_segment(or1), '20260601')
        self.assertEqual(_date_segment(or3), '20260602')

    def test_strictly_monotonic_over_many_days(self):
        seen = []
        for day in range(1, 6):  # five consecutive days
            for _ in range(3):   # three sales each
                seen.append(_nnnn(self._generate_on(2026, 7, day)))
        # Strictly increasing with no resets.
        self.assertEqual(seen, list(range(1, len(seen) + 1)))

    def test_new_day_row_seeds_from_global_high_water_mark(self):
        # Simulate historical data: a stale future-dated counter row.
        OfficialReceiptCounter.objects.create(date='2026-01-15', counter=99)
        or_next = self._generate_on(2026, 8, 1)
        # Must exceed the highest existing counter regardless of date ordering.
        self.assertEqual(_nnnn(or_next), 100)
