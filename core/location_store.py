"""Small SQLite repository for named anchor coordinates. No device or UI logic."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

from .models import Coordinate, SavedLocation

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "locations.sqlite3"


class LocationExistsError(ValueError):
    """Saving a duplicate name must not silently overwrite a bookmark."""


class LocationStore:
    """Exact, case-sensitive names; strip only leading and trailing whitespace.

    Both front-ends call this from the event-loop thread, but nothing enforces
    that: FastAPI runs a non-async route handler in a worker thread, and
    TestClient always does. Rather than rely on the convention holding, the
    connection allows cross-thread use and every statement is serialized by a
    lock - which also protects the implicit transaction `with self._connection`
    opens.
    """

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        try:
            with self._connection:
                self._connection.execute("""
                    CREATE TABLE IF NOT EXISTS saved_locations (
                        name TEXT COLLATE BINARY NOT NULL PRIMARY KEY,
                        latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                        longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180)
                    )
                """)
        except BaseException:
            self._connection.close()
            raise

    @staticmethod
    def _name(name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("name cannot be empty")
        return name

    def save(self, name: str, coordinate: Coordinate) -> SavedLocation:
        name = self._name(name)
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO saved_locations (name, latitude, longitude) VALUES (?, ?, ?)",
                    (name, coordinate.latitude, coordinate.longitude),
                )
        except sqlite3.IntegrityError as exc:
            if self.get(name) is not None:
                raise LocationExistsError(f"name already saved: {name}") from exc
            raise
        return SavedLocation(name, coordinate)

    def get(self, name: str) -> SavedLocation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT name, latitude, longitude FROM saved_locations WHERE name = ?",
                (name.strip(),),
            ).fetchone()
        return SavedLocation(row[0], Coordinate(row[1], row[2])) if row else None

    def list_all(self) -> list[SavedLocation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT name, latitude, longitude FROM saved_locations ORDER BY name COLLATE BINARY"
            ).fetchall()
        return [SavedLocation(name, Coordinate(lat, lon)) for name, lat, lon in rows]

    def delete(self, name: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM saved_locations WHERE name = ?", (name.strip(),)
            )
        return cursor.rowcount != 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> LocationStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
