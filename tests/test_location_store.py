from pathlib import Path
import tempfile
import unittest

from core.location_store import LocationExistsError, LocationStore
from core.models import Coordinate


class LocationStoreTests(unittest.TestCase):
    def test_data_survives_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "locations.sqlite3"
            with LocationStore(path) as store:
                store.save("  Campus  ", Coordinate(40.123456789, -74.123456789))
            with LocationStore(path) as store:
                self.assertEqual(store.get("Campus").coordinate, Coordinate(40.123456789, -74.123456789))
                self.assertEqual(len(store.list_all()), 1)

    def test_names_are_exact_case_sensitive_and_only_edge_whitespace_is_removed(self):
        with LocationStore(":memory:") as store:
            first = store.save(" Home  Base ", Coordinate(1, 2))
            store.save("home  Base", Coordinate(3, 4))
            self.assertEqual(first.name, "Home  Base")
            self.assertEqual(store.get("  Home  Base  "), first)
            self.assertIsNone(store.get("Home Base"))
            self.assertIsNone(store.get("HOME  BASE"))
            self.assertIsNone(store.get("Home  Bas"))
            self.assertEqual(len(store.list_all()), 2)

    def test_duplicate_name_does_not_overwrite(self):
        with LocationStore(":memory:") as store:
            original = store.save("Home", Coordinate(1, 2))
            with self.assertRaises(LocationExistsError):
                store.save(" Home ", Coordinate(3, 4))
            self.assertEqual(store.get("Home"), original)

    def test_delete_is_exact_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locations.sqlite3"
            with LocationStore(path) as store:
                store.save("Home", Coordinate(1, 2))
                self.assertFalse(store.delete("home"))
                self.assertTrue(store.delete(" Home "))
                self.assertFalse(store.delete("Home"))
            with LocationStore(path) as store:
                self.assertEqual(store.list_all(), [])

    def test_sql_metacharacters_are_literal_names(self):
        with LocationStore(":memory:") as store:
            name = "Home'; DROP TABLE saved_locations;--"
            saved = store.save(name, Coordinate(1, 2))
            self.assertEqual(store.get(name), saved)
            self.assertFalse(store.delete("' OR 1=1 --"))
            self.assertEqual(len(store.list_all()), 1)

    def test_empty_names_rejected(self):
        with LocationStore(":memory:") as store:
            with self.assertRaises(ValueError):
                store.save("  ", Coordinate(1, 2))


if __name__ == "__main__":
    unittest.main()
