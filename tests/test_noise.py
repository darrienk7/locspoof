import math
import random
import statistics
import unittest

from core.models import Coordinate
from core.noise import GpsNoise, NoiseParameters, _reflect


class UnitInnovations:
    """A deterministic +1 standard-normal innovation for equation checks."""

    def gauss(self, mean, sigma):
        return mean + sigma


def lag_one(values):
    mean = statistics.mean(values)
    return sum((a - mean) * (b - mean) for a, b in zip(values, values[1:])) / sum(
        (value - mean) ** 2 for value in values)


class NoiseTests(unittest.TestCase):
    anchor = Coordinate(40.69, -74.04)

    def test_components_follow_equations_and_sum_from_anchor(self):
        noise = GpsNoise(radius_m=2, rng=UnitInnovations())
        point = noise.jitter(self.anchor, dt_s=4)
        phi = math.exp(-4 / 30)
        first_gm = 0.35 * math.sqrt(1 - phi ** 2) * 2
        components = noise.last_components
        self.assertAlmostEqual(components.gauss_markov_m[0], first_gm)
        self.assertAlmostEqual(components.white_m[0], 0.3)
        self.assertAlmostEqual(components.random_walk_m[0], 0.2)
        self.assertAlmostEqual(noise.last_offset_m[0], first_gm + 0.3 + 0.2)
        self.assertAlmostEqual((point.latitude - self.anchor.latitude) * 111320,
                               noise.last_offset_m[0], places=7)
        noise.jitter(self.anchor, dt_s=4)
        self.assertAlmostEqual(noise.last_components.gauss_markov_m[0], phi * first_gm + first_gm)
        self.assertAlmostEqual(noise.last_components.random_walk_m[0], 0.4)

    def test_gauss_markov_stationary_variance_and_correlation(self):
        parameters = NoiseParameters(correlation_time_s=5, gauss_markov_fraction=0.2,
                                     white_fraction=0, walk_step_fraction=0)
        noise = GpsNoise(radius_m=1, parameters=parameters, rng=random.Random(17))
        values = []
        for index in range(31000):
            noise.jitter(self.anchor)
            if index >= 1000:
                values.append(noise.last_components.gauss_markov_m[0])
        self.assertAlmostEqual(statistics.mean(values), 0, delta=0.02)
        self.assertAlmostEqual(statistics.pvariance(values), 0.2 ** 2, delta=0.004)
        self.assertAlmostEqual(lag_one(values), math.exp(-1 / 5), delta=0.02)

    def test_white_noise_has_expected_variance_without_temporal_correlation(self):
        parameters = NoiseParameters(gauss_markov_fraction=0, white_fraction=0.2, walk_step_fraction=0)
        noise = GpsNoise(radius_m=1, parameters=parameters, rng=random.Random(51))
        values = []
        for _ in range(20000):
            noise.jitter(self.anchor)
            values.append(noise.last_components.white_m[0])
        self.assertAlmostEqual(statistics.mean(values), 0, delta=0.01)
        self.assertAlmostEqual(statistics.pvariance(values), 0.04, delta=0.002)
        self.assertAlmostEqual(lag_one(values), 0, delta=0.03)

    def test_walk_diffusion_scales_with_sqrt_elapsed_time(self):
        noise = GpsNoise(radius_m=1, rng=UnitInnovations())
        noise.jitter(self.anchor, dt_s=1)
        one_second = noise.last_components.random_walk_m[0]
        noise.reset()
        noise.jitter(self.anchor, dt_s=4)
        self.assertAlmostEqual(noise.last_components.random_walk_m[0], 2 * one_second)

    def test_long_run_stays_centered_and_bounded(self):
        noise = GpsNoise(rng=random.Random(101))
        north = []
        east = []
        for _ in range(60000):
            point = noise.jitter(self.anchor)
            n, e = noise.last_offset_m
            north.append(n)
            east.append(e)
            self.assertLessEqual(abs(n), 3)
            self.assertLessEqual(abs(e), 3)
            self.assertLessEqual(abs(noise.last_components.random_walk_m[0]), 3 * 0.35 + 1e-12)
            self.assertLessEqual(abs(point.latitude - self.anchor.latitude), 3 / 111320 + 1e-12)
        self.assertAlmostEqual(statistics.mean(north), 0, delta=0.15)
        self.assertAlmostEqual(statistics.mean(east), 0, delta=0.15)
        self.assertEqual(self.anchor, Coordinate(40.69, -74.04))

    def test_reflection_handles_multiple_boundary_crossings(self):
        for value, expected in [(0, 0), (1, 1), (2, 0), (3, -1), (-2, 0), (9, 1)]:
            self.assertAlmostEqual(_reflect(value, 1), expected)
        self.assertEqual(_reflect(100, 0), 0)

    def test_anchor_change_resets_state_without_reseeding(self):
        rng = random.Random(42)
        noise = GpsNoise(rng=rng)
        for _ in range(10):
            noise.jitter(self.anchor)
        fresh_rng = random.Random()
        fresh_rng.setstate(rng.getstate())
        fresh = GpsNoise(rng=fresh_rng)
        other = Coordinate(-20, 120)
        self.assertEqual(noise.jitter(other), fresh.jitter(other))
        self.assertEqual(noise.last_components, fresh.last_components)

    def test_live_radius_change_scales_states_and_output(self):
        a = GpsNoise(rng=random.Random(8))
        b = GpsNoise(rng=random.Random(8))
        a.jitter(self.anchor)
        b.jitter(self.anchor)
        b.radius_m = 0.3
        a.jitter(self.anchor)
        b.jitter(self.anchor)
        for original, scaled in zip(a.last_offset_m, b.last_offset_m):
            self.assertAlmostEqual(scaled, original / 10)

    def test_invalid_parameters_rejected_before_state_changes(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GpsNoise(radius_m=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                GpsNoise(interval_s=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                GpsNoise().jitter(self.anchor, dt_s=value)
        with self.assertRaises(ValueError):
            NoiseParameters(white_fraction=-1)

    def test_poles_and_antimeridian_produce_valid_coordinates(self):
        noise = GpsNoise(rng=random.Random(9))
        for anchor in (Coordinate(90, 180), Coordinate(-90, -180), Coordinate(0, 179.999999)):
            for _ in range(100):
                result = noise.jitter(anchor)
                self.assertTrue(-90 <= result.latitude <= 90)
                self.assertTrue(-180 <= result.longitude < 180)


if __name__ == "__main__":
    unittest.main()
