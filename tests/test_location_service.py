import asyncio
import random
import unittest
from unittest.mock import AsyncMock

from core.location_service import LocationService
from core.models import Coordinate
from core.noise import GpsNoise, NoiseComponents


class LocationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.noise = GpsNoise(interval_s=60, rng=random.Random(12))
        self.service = LocationService(None, noise=self.noise)
        self.sim = AsyncMock()
        self.service._sim = self.sim

    async def asyncTearDown(self):
        await self.service.detach()

    async def test_setting_even_same_anchor_starts_fresh_noise_state(self):
        anchor = Coordinate(1, 2)
        await self.service.set_location(anchor)
        self.noise.jitter(anchor)
        self.assertNotEqual(self.noise.last_offset_m, (0, 0))
        await self.service.set_location(anchor)
        self.assertEqual(self.noise.last_components, NoiseComponents())
        self.assertEqual(self.service.current, anchor)
        self.assertTrue(self.service.noise_active)
        self.assertEqual(self.service.tick_count, 0)

    async def test_noise_off_restores_exact_anchor_and_resets_components(self):
        anchor = Coordinate(1, 2)
        await self.service.set_location(anchor)
        self.noise.jitter(anchor)
        await self.service.set_noise_enabled(False)
        self.sim.set.assert_awaited_with(1, 2)
        self.assertFalse(self.service.noise_active)
        self.assertEqual(self.noise.last_components, NoiseComponents())

    async def test_clear_and_detach_reset_noise(self):
        anchor = Coordinate(1, 2)
        await self.service.set_location(anchor)
        self.noise.jitter(anchor)
        await self.service.clear_location()
        self.assertIsNone(self.service.anchor)
        self.assertIsNone(self.service.current)
        self.assertEqual(self.noise.last_offset_m, (0, 0))
        self.assertFalse(self.service.noise_active)
        self.noise.jitter(anchor)
        await self.service.detach()
        self.assertEqual(self.noise.last_components, NoiseComponents())

    async def test_failed_new_anchor_keeps_old_confirmed_location(self):
        old = Coordinate(1, 2)
        await self.service.set_location(old)
        self.sim.set.side_effect = RuntimeError("disconnected")
        with self.assertRaises(RuntimeError):
            await self.service.set_location(Coordinate(3, 4))
        self.assertEqual(self.service.anchor, old)
        self.assertEqual(self.service.current, old)

    async def test_failed_clear_keeps_anchor_for_retry(self):
        old = Coordinate(1, 2)
        await self.service.set_location(old)
        self.sim.clear.side_effect = RuntimeError("disconnected")
        with self.assertRaises(RuntimeError):
            await self.service.clear_location()
        self.assertEqual(self.service.anchor, old)

    async def test_ticker_updates_from_anchor_and_stops_on_write_failure(self):
        anchor = Coordinate(1, 2)
        self.noise.interval_s = 0.001
        observed = asyncio.Event()
        calls = []
        async def write(lat, lon):
            calls.append(Coordinate(lat, lon))
            if len(calls) >= 4:
                observed.set()
                raise RuntimeError("disconnected")
        self.sim.set.side_effect = write
        await self.service.set_location(anchor)
        await asyncio.wait_for(observed.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(self.service.noise_active)
        self.assertEqual(self.service.anchor, anchor)
        self.assertEqual(self.service.tick_count, 2)
        self.assertEqual(self.service.current, calls[-2])
        for coord in calls:
            self.assertLessEqual(abs(coord.latitude - anchor.latitude), 3 / 111320 + 1e-12)

    async def test_anchor_change_cancels_inflight_tick_before_new_write(self):
        old, new = Coordinate(1, 2), Coordinate(3, 4)
        self.noise.interval_s = 0.001
        tick_entered = asyncio.Event()
        tick_cancelled = asyncio.Event()
        writes = []
        async def write(lat, lon):
            coord = Coordinate(lat, lon)
            if coord not in (old, new):
                tick_entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    tick_cancelled.set()
                    raise
            writes.append(coord)
        self.sim.set.side_effect = write
        await self.service.set_location(old)
        await asyncio.wait_for(tick_entered.wait(), timeout=1)
        await self.service.set_location(new)
        self.assertTrue(tick_cancelled.is_set())
        self.assertEqual(writes, [old, new])
        self.assertEqual(self.service.anchor, new)
        self.assertEqual(self.service.current, new)


if __name__ == "__main__":
    unittest.main()
