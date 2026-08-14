"""FEATURE-061 — counted_at + stale-shift signalling.

PROD's shifts routinely ran 14-31 hours and were closed the NEXT day
(shift 37: opened 08-11 05:31, closed 08-12 12:48). The Z is stamped with
business_date = the shift's OPEN date, so the report asserts the count belongs
to a day on which it was not taken — and by the manager's own account the owner
had already collected the cash in between. A count against an emptied drawer
cannot reconcile, and nothing in the record showed that had happened.

Verified before writing this: long shifts do NOT scatter sales across calendar
dates — every shift's transactions fall on one date — so daily figures were
never wrong. Only the COUNT was late. This ticket records that, and prompts.

What it deliberately does NOT do: auto-close (that would write an immutable Z
with no human count — fabricating the very number this work protects) or block
selling (the worst failure a cafe register can have).
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone as dj_tz
from rest_framework.test import APITestCase

from pos.models import BusinessProfile, Item, Shift, User
from pos.serializers import ShiftSerializer
from pos.services import close_shift_and_finalize_z


def _bp():
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = False
    bp.track_inventory = False
    bp.printer_mode = 'disabled'
    bp.save()
    return bp


class CountedAtTests(APITestCase):
    def setUp(self):
        _bp()
        self.user = User.objects.create_user(
            username='c1', password='x', role='cashier'
        )
        Item.objects.create(name='X', price=Decimal('10.00'), stock=100)

    def _shift_opened_days_ago(self, days):
        s = Shift.objects.create(
            cashier=self.user, opening_cash=Decimal('2000.00'), is_open=True
        )
        # opened_at is auto_now_add, so rewrite it directly.
        Shift.objects.filter(pk=s.pk).update(
            opened_at=dj_tz.now() - timedelta(days=days)
        )
        return Shift.objects.get(pk=s.pk)

    def test_counted_at_is_stamped_at_close(self):
        shift = self._shift_opened_days_ago(0)
        before = dj_tz.now()
        z = close_shift_and_finalize_z(shift.id, Decimal('2000.00'), self.user)
        after = dj_tz.now()

        self.assertIsNotNone(z.counted_at)
        self.assertGreaterEqual(z.counted_at, before)
        self.assertLessEqual(z.counted_at, after)

    def test_counted_at_differs_from_business_date_on_a_late_close(self):
        """The PROD case: a shift opened yesterday, counted today. The Z must
        carry both dates so the gap is visible rather than implied."""
        shift = self._shift_opened_days_ago(1)

        z = close_shift_and_finalize_z(shift.id, Decimal('2000.00'), self.user)

        self.assertEqual(z.business_date, dj_tz.localdate(shift.opened_at))
        self.assertNotEqual(dj_tz.localdate(z.counted_at), z.business_date)

    def test_NEGATIVE_CONTROL_same_day_close_has_matching_dates(self):
        """Proves the assertion above is about lateness, not about the fields
        simply always disagreeing."""
        shift = self._shift_opened_days_ago(0)

        z = close_shift_and_finalize_z(shift.id, Decimal('2000.00'), self.user)

        self.assertEqual(dj_tz.localdate(z.counted_at), z.business_date)


class StaleShiftSignalTests(APITestCase):
    """The serializer decides staleness server-side; the kiosk only renders it.
    Timezone reasoning belongs where TIME_ZONE lives."""

    def setUp(self):
        _bp()
        self.user = User.objects.create_user(
            username='c2', password='x', role='cashier'
        )

    def _shift(self, days_ago):
        s = Shift.objects.create(
            cashier=self.user, opening_cash=Decimal('2000.00'), is_open=True
        )
        Shift.objects.filter(pk=s.pk).update(
            opened_at=dj_tz.now() - timedelta(days=days_ago)
        )
        return Shift.objects.get(pk=s.pk)

    def test_shift_from_yesterday_is_flagged(self):
        data = ShiftSerializer(self._shift(1)).data
        self.assertTrue(data['spans_business_date'])
        self.assertGreaterEqual(data['hours_open'], 23.0)

    def test_NEGATIVE_CONTROL_todays_shift_is_not_flagged(self):
        data = ShiftSerializer(self._shift(0)).data
        self.assertFalse(data['spans_business_date'])

    def test_hours_open_tracks_a_long_shift(self):
        """PROD shift 37 ran 31 hours."""
        s = self._shift(0)
        Shift.objects.filter(pk=s.pk).update(
            opened_at=dj_tz.now() - timedelta(hours=31)
        )
        data = ShiftSerializer(Shift.objects.get(pk=s.pk)).data
        self.assertAlmostEqual(data['hours_open'], 31.0, delta=0.2)
        self.assertTrue(data['spans_business_date'])

    def test_closed_shift_measures_to_its_close_not_to_now(self):
        """A finished shift's duration must freeze, or every old Z would look
        worse the longer ago it happened."""
        # 🔴 The open time is PINNED to 08:00 local, not derived from now().
        #
        # This test used `now() - 2 days` and then closed 3 hours later, and
        # asserted the shift does not span two business dates. That holds only
        # while the suite is run before 21:00 local: run it at 21:37 and the
        # fixture describes a shift opened 21:37 and closed 00:37, which spans
        # two dates CORRECTLY, and the test fails on healthy code.
        #
        # It went green at 19:19 and red at 21:37 on 2026-08-14 with no code
        # change between. Caught before the 22:00 deploy pre-flight, which
        # would have gone red on a clean tree. The product code was never
        # wrong — the fixture was.
        opened_local = dj_tz.localtime(dj_tz.now()).replace(
            hour=8, minute=0, second=0, microsecond=0
        ) - timedelta(days=2)
        s = self._shift(2)
        Shift.objects.filter(pk=s.pk).update(
            opened_at=opened_local,
            closed_at=opened_local + timedelta(hours=3),
            is_open=False,
        )
        data = ShiftSerializer(Shift.objects.get(pk=s.pk)).data
        self.assertAlmostEqual(data['hours_open'], 3.0, delta=0.2)
        self.assertFalse(data['spans_business_date'])

    def test_selling_is_never_blocked_by_a_stale_shift(self):
        """🔴 The guarantee that matters most. A register that refuses to sell
        is a worse failure than a late count, so staleness must stay purely
        informational — the API exposes a flag and nothing else changes."""
        stale = self._shift(2)
        data = ShiftSerializer(stale).data

        self.assertTrue(data['spans_business_date'])
        # The shift is still open and still usable.
        self.assertTrue(data['is_open'])
        self.assertTrue(Shift.objects.get(pk=stale.pk).is_open)

    def test_no_auto_close_happens(self):
        """Nothing may finalize a Z without a human count — an auto-generated
        Z is a fabricated number."""
        from pos.models import ZReport
        stale = self._shift(5)
        ShiftSerializer(stale).data  # merely observing must not act

        self.assertTrue(Shift.objects.get(pk=stale.pk).is_open)
        self.assertEqual(ZReport.objects.count(), 0)
