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

    # ------------------------------------------------------------------
    # Moving
    # ------------------------------------------------------------------

    async def test_bare_coordinates_move_in_one_input_and_save_nothing(self):
        output, read = await self.drive(["40.123456789, -74.1", "q"])
        self.location._sim.set.assert_awaited_once_with(40.123456789, -74.1)
        self.assertEqual(self.saved.list_all(), [])
        self.assertIn("successful spoof", output)
        # No second prompt: every read used the main label.
        self.assertTrue(all(call.args[0] == "  loc> " for call in read.call_args_list))

    async def test_inline_as_saves_the_exact_anchor(self):
        await self.drive(["40.123456789, -74.1 as Home  Base", "q"])
        self.assertEqual(
            self.saved.get("Home  Base").coordinate, Coordinate(40.123456789, -74.1)
        )
        self.location._sim.set.assert_awaited_once_with(40.123456789, -74.1)

    async def test_as_separator_is_case_insensitive(self):
        await self.drive(["1 2 AS Home", "q"])
        self.assertIsNotNone(self.saved.get("Home"))

    async def test_as_without_coordinates_is_rejected(self):
        output, _ = await self.drive(["somewhere as Home", "q"])
        self.assertIn("only works with coordinates", output)
        self.assertEqual(self.saved.list_all(), [])
        self.location._sim.set.assert_not_awaited()

    async def test_saved_name_uses_exact_full_case(self):
        self.saved.save("Home  Base", Coordinate(1, 2))
        output, read = await self.drive(
            ["home  Base", "Home Base", "Home  Bas", "  Home  Base  ", "q"]
        )
        self.assertEqual(output.count("? not understand"), 3)
        self.location._sim.set.assert_awaited_once_with(1, 2)
        self.assertTrue(all(call.args[0] == "  loc> " for call in read.call_args_list))

    # ------------------------------------------------------------------
    # save command
    # ------------------------------------------------------------------

    async def test_save_command_bookmarks_the_current_anchor(self):
        await self.drive(["1 2", "save Home", "q"])
        self.assertEqual(self.saved.get("Home").coordinate, Coordinate(1, 2))

    async def test_save_before_any_location_is_refused(self):
        output, _ = await self.drive(["save Home", "q"])
        self.assertIn("nothing to save", output)
        self.assertEqual(self.saved.list_all(), [])

    async def test_save_with_no_name_prints_usage(self):
        output, _ = await self.drive(["1 2", "save", "q"])
        self.assertIn("usage: save <name>", output)
        self.assertEqual(self.saved.list_all(), [])

    async def test_duplicate_name_is_refused_without_overwrite(self):
        self.saved.save("Home", Coordinate(3, 4))
        output, _ = await self.drive(["1 2 as Home", "5 6 as home", "q"])
        self.assertIn("already saved", output)
        self.assertEqual(self.saved.get("Home").coordinate, Coordinate(3, 4))
        self.assertEqual(self.saved.get("home").coordinate, Coordinate(5, 6))

    async def test_reserved_names_are_refused(self):
        for name in ("clear", "noise", "list", "save", "q", "1 2", "a as b"):
            with self.subTest(name=name):
                output, _ = await self.drive([f"7 8 as {name}", "q"])
                self.assertIn("pick another name", output)
                self.assertIsNone(self.saved.get(name))

    async def test_failed_device_write_does_not_save(self):
        self.location._sim.set.side_effect = RuntimeError("disconnected")
        output, _ = await self.drive(["1 2 as Home", "q"])
        self.assertIn("failed to set location", output)
        self.assertEqual(self.saved.list_all(), [])

    # ------------------------------------------------------------------
    # Invalid input
    # ------------------------------------------------------------------

    async def test_out_of_range_names_the_offending_axis(self):
        output, _ = await self.drive(["91 0", "0 -181", "q"])
        self.assertIn("Latitude out of range", output)
        self.assertIn("Longitude out of range", output)
        self.location._sim.set.assert_not_awaited()

    async def test_non_finite_coordinates_are_rejected_as_such(self):
        output, _ = await self.drive(["nan 1", "1 inf", "-inf 0", "q"])
        self.assertEqual(output.count("must be finite"), 3)
        self.assertNotIn("out of range", output)
        self.location._sim.set.assert_not_awaited()

    async def test_malformed_input_falls_through_to_name_lookup(self):
        output, _ = await self.drive(["bad", "1 2 3", "q"])
        self.assertEqual(output.count("? not understand"), 2)
        self.location._sim.set.assert_not_awaited()
        self.assertEqual(self.saved.list_all(), [])

    # ------------------------------------------------------------------
    # list / delete
    # ------------------------------------------------------------------

    async def test_list_shows_coordinates_and_deletes_only_exact_name(self):
        self.saved.save("Home", Coordinate(1, 2))
        self.saved.save("home", Coordinate(3, 4))
        output, _ = await self.drive(["list", "delete HOME", "delete Home", "back", "q"])
        self.assertIn("Home  |  1.0, 2.0", output)
        self.assertIn("home  |  3.0, 4.0", output)
        self.assertIn("no saved location named 'HOME'", output)
        self.assertIsNone(self.saved.get("Home"))
        self.assertIsNotNone(self.saved.get("home"))
        self.location._sim.set.assert_not_awaited()

    async def test_bare_delete_prints_usage(self):
        output, _ = await self.drive(["list", "delete", "back", "q"])
        self.assertIn("usage: delete <exact name>", output)

    async def test_quit_from_list_exits_the_app(self):
        _, read = await self.drive(["list", "q"])
        self.assertEqual(len(read.call_args_list), 2)

    # ------------------------------------------------------------------
    # noise / clear
    # ------------------------------------------------------------------

    async def test_clear_and_noise_commands_are_preserved(self):
        output, _ = await self.drive(
            ["1 2", "noise 5", "noise on", "noise off", "noise", "clear", "q"]
        )
        self.assertEqual(self.location.noise.radius_m, 5)
        self.assertFalse(self.location.noise_enabled)
        self.assertIn("noise: OFF", output)
        self.location._sim.clear.assert_awaited_once()

    async def test_nonfinite_noise_radius_does_not_poison_state(self):
        await self.drive(["noise nan", "noise inf", "q"])
        self.assertEqual(self.location.noise.radius_m, 3)

    async def test_eof_quits_cleanly(self):
        await self.drive([EOFError()])
        self.location._sim.set.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
