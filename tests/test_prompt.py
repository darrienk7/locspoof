from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import AsyncMock, patch

from cli import prompt
from core.location_service import LocationService
from core.location_store import LocationStore
from core.models import Coordinate


class PromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = LocationStore(":memory:")
        self.location = LocationService(None, noise_enabled=False)
        self.location._sim = AsyncMock()

    async def asyncTearDown(self):
        await self.location.detach()
        self.saved.close()

    async def drive(self, lines):
        output = io.StringIO()
        with patch("builtins.input", side_effect=lines) as read, redirect_stdout(output):
            await prompt.run(self.location, self.saved)
        return output.getvalue(), read

    async def test_successful_coordinates_prompt_then_save_exact_anchor(self):
        output, read = await self.drive(["40.123456789, -74.1", "  Home  Base  ", "q"])
        self.assertEqual(self.saved.get("Home  Base").coordinate, Coordinate(40.123456789, -74.1))
        self.location._sim.set.assert_awaited_once_with(40.123456789, -74.1)
        self.assertIn("Save a name", read.call_args_list[1].args[0])
        self.assertIn("successful spoof", output)

    async def test_discard_is_case_insensitive_and_still_moves(self):
        await self.drive(["1 2", " DiScArD ", "q"])
        self.location._sim.set.assert_awaited_once_with(1.0, 2.0)
        self.assertEqual(self.saved.list_all(), [])

    async def test_saved_name_uses_exact_full_case_without_another_save_prompt(self):
        self.saved.save("Home  Base", Coordinate(1, 2))
        output, read = await self.drive(["home  Base", "Home Base", "Home  Bas", "  Home  Base  ", "q"])
        self.assertEqual(output.count("? not understand"), 3)
        self.location._sim.set.assert_awaited_once_with(1, 2)
        self.assertTrue(all(call.args[0] == "  loc> " for call in read.call_args_list))
        self.assertNotIn("try:", output)

    async def test_invalid_coordinates_print_only_requested_error(self):
        output, _ = await self.drive(["91 0", "0 -181", "nan 1", "1 inf", "bad", "1 2 3", "q"])
        self.assertEqual(output.count("? not understand"), 6)
        self.assertNotIn("out of range", output)
        self.location._sim.set.assert_not_awaited()
        self.assertEqual(self.saved.list_all(), [])

    async def test_list_shows_coordinates_and_deletes_only_exact_name(self):
        self.saved.save("Home", Coordinate(1, 2))
        self.saved.save("home", Coordinate(3, 4))
        output, _ = await self.drive(["list", "delete HOME", "delete Home", "back", "q"])
        self.assertIn("Home  |  1.0, 2.0", output)
        self.assertIn("home  |  3.0, 4.0", output)
        self.assertIn("? not understand", output)
        self.assertIsNone(self.saved.get("Home"))
        self.assertIsNotNone(self.saved.get("home"))
        self.location._sim.set.assert_not_awaited()

    async def test_list_at_save_prompt_returns_to_pending_coordinates(self):
        self.saved.save("Old", Coordinate(3, 4))
        await self.drive(["1 2", "list", "delete Old", "back", "New", "q"])
        self.assertIsNone(self.saved.get("Old"))
        self.assertEqual(self.saved.get("New").coordinate, Coordinate(1, 2))

    async def test_duplicate_name_reprompts_without_overwrite(self):
        self.saved.save("Home", Coordinate(3, 4))
        output, _ = await self.drive(["1 2", "Home", "home", "q"])
        self.assertIn("name already saved", output)
        self.assertEqual(self.saved.get("Home").coordinate, Coordinate(3, 4))
        self.assertEqual(self.saved.get("home").coordinate, Coordinate(1, 2))

    async def test_failed_device_write_does_not_save(self):
        self.location._sim.set.side_effect = RuntimeError("disconnected")
        output, _ = await self.drive(["1 2", "Home", "q"])
        self.assertIn("failed to set location", output)
        self.assertEqual(self.saved.list_all(), [])

    async def test_eof_or_quit_during_save_abandons_pending_move(self):
        for choice in (EOFError(), "q"):
            await self.drive(["1 2", choice])
        self.location._sim.set.assert_not_awaited()
        self.assertEqual(self.saved.list_all(), [])

    async def test_clear_and_noise_commands_are_preserved(self):
        output, _ = await self.drive(["1 2", "discard", "noise 5", "noise on", "noise off", "noise", "clear", "q"])
        self.assertEqual(self.location.noise.radius_m, 5)
        self.assertFalse(self.location.noise_enabled)
        self.assertIn("noise: OFF", output)
        self.location._sim.clear.assert_awaited_once()

    async def test_nonfinite_noise_radius_does_not_poison_state(self):
        await self.drive(["noise nan", "noise inf", "q"])
        self.assertEqual(self.location.noise.radius_m, 3)


if __name__ == "__main__":
    unittest.main()
