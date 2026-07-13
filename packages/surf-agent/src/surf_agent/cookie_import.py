from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .config import CookieSourceConfig, write_config
from .errors import SurfAgentError

DESTINATION_PROFILE = "Default"
_COOKIE_CANDIDATES = (Path("Cookies"), Path("Network") / "Cookies")
_SIDECARS = ("-wal", "-journal", "-shm")


@dataclass(frozen=True)
class CookieImportResult:
    imported_rows: int = 0
    skipped: bool = False
    destination: Path | None = None


class CookieImporter:
    """Safely merges selected encrypted Chromium cookie rows into an inactive profile."""

    def __init__(
        self,
        *,
        config: CookieSourceConfig,
        destination_root: Path,
        state_root: Path,
        destination_family: str | None,
        platform_name: str | None = None,
        process_inspector: Callable[[Path], bool] | None = None,
        max_backup_attempts: int = 2,
    ) -> None:
        self.config = config
        self.destination_root = destination_root.expanduser()
        self.state_root = state_root
        self.destination_family = destination_family
        self.platform_name = platform_name if platform_name is not None else sys.platform
        self.process_inspector = process_inspector or (lambda _path: False)
        self.max_backup_attempts = max_backup_attempts
        self.publication_hook: Callable[[str, Path], None] = lambda _boundary, _path: None

    @property
    def state_file(self) -> Path:
        return self.state_root / "cookie-import.json"

    def run(self, force: bool) -> CookieImportResult:
        self._validate_platform_and_identity()
        source_profile = self._source_profile()
        source_db, relative_candidate = discover_cookie_database(source_profile)
        self._validate_runtime_source_paths(source_profile, source_db)
        destination_profile = self.destination_root / DESTINATION_PROFILE
        destination_db = discover_destination_cookie_database(destination_profile)
        self._validate_paths(source_db, destination_profile)
        self._ensure_destination_inactive()
        fingerprint = self._fingerprint(source_db, relative_candidate)
        if not force and self._stored_fingerprint() == fingerprint:
            return CookieImportResult(skipped=True, destination=destination_db or destination_profile / relative_candidate)

        source_os_crypt = read_os_crypt(self.config.root / "Local State", required=True)
        destination_local_state_exists, existing_destination_os_crypt = read_destination_os_crypt(self.destination_root / "Local State")
        if destination_local_state_exists and existing_destination_os_crypt != source_os_crypt:
            raise SurfAgentError("destination Local State encryption metadata does not match the cookie source")

        for attempt in range(self.max_backup_attempts):
            self._validate_runtime_source_paths(source_profile, source_db)
            before = self._fingerprint(source_db, relative_candidate)
            staging = self._backup_source(source_db)
            try:
                self._validate_runtime_source_paths(source_profile, source_db)
                validate_cookie_database(staging)
                after = self._fingerprint(source_db, relative_candidate)
                if before != after:
                    if attempt + 1 == self.max_backup_attempts:
                        raise SurfAgentError("cookie source changed while it was being backed up; retry the import")
                    continue
                if destination_db is None:
                    count = count_scoped_rows(staging, self.config)
                    installed = self._install_new_destination(
                        staging=staging,
                        relative_candidate=relative_candidate,
                        source_os_crypt=source_os_crypt,
                        destination_local_state_absent=not destination_local_state_exists,
                    )
                else:
                    count = self._merge_existing_destination(staging, destination_db)
                    installed = destination_db
                committed_fingerprint = self._fingerprint(source_db, relative_candidate)
                if committed_fingerprint != after:
                    # Source changed after the backup. The committed merge is safe, but do not
                    # record it as current so the next automatic startup refreshes it.
                    return CookieImportResult(imported_rows=count, destination=installed)
                self._write_fingerprint(committed_fingerprint)
                return CookieImportResult(imported_rows=count, destination=installed)
            finally:
                _unlink_tree_file(staging)
        raise SurfAgentError("cookie source backup could not stabilize")

    def _ensure_destination_inactive(self) -> None:
        destination = self.destination_root.resolve() if self.destination_root.exists() else self.destination_root.absolute()
        if self.process_inspector(destination):
            raise SurfAgentError("destination Surf browser profile is active; stop it before importing cookies")

    def _validate_platform_and_identity(self) -> None:
        if not self.platform_name.startswith("linux"):
            raise SurfAgentError("live cookie import is supported only on Linux")
        if self.destination_family is None:
            raise SurfAgentError("could not prove the browser family of the Surf destination")
        if self.config.family != self.destination_family:
            raise SurfAgentError("cookie source browser family does not match the Surf Chrome profile")
        try:
            source_owner = self.config.root.stat().st_uid
            if source_owner != os.getuid():
                raise SurfAgentError("cookie source is not owned by the current OS user")
            owner_anchor = self.destination_root if self.destination_root.exists() else self.destination_root.parent
            if owner_anchor.exists() and owner_anchor.stat().st_uid != source_owner:
                raise SurfAgentError("cookie source and destination profiles have different owners")
        except OSError as exc:
            raise SurfAgentError(f"could not inspect cookie profile ownership: {exc}") from exc

    def _source_profile(self) -> Path:
        root = self.config.root
        profile = root / self.config.profile
        try:
            root_stat = root.lstat()
            if stat.S_ISLNK(root_stat.st_mode):
                raise SurfAgentError("cookie source root is a symlink")
            resolved_root = root.resolve(strict=True)
            profile_stat = profile.lstat()
            if stat.S_ISLNK(profile_stat.st_mode):
                raise SurfAgentError("cookie source profile is a symlink")
            resolved_profile = profile.resolve(strict=True)
            if not stat.S_ISDIR(profile_stat.st_mode) or resolved_profile.parent != resolved_root:
                raise SurfAgentError("cookie source profile is no longer a safe child of its source root")
        except SurfAgentError:
            raise
        except OSError as exc:
            raise SurfAgentError(f"could not inspect cookie source profile: {exc}") from exc
        return profile

    def _validate_runtime_source_paths(self, profile: Path, source_db: Path) -> None:
        # Persisted paths may be replaced after configuration. Never follow a link
        # while reading live browser storage because that can redirect cookie import.
        self._source_profile()
        _require_regular_non_symlink(source_db, "cookie source database")
        for suffix in _SIDECARS:
            sidecar = Path(f"{source_db}{suffix}")
            if sidecar.exists() or sidecar.is_symlink():
                _require_regular_non_symlink(sidecar, "cookie source sidecar")

    def _validate_paths(self, source_db: Path, destination_profile: Path) -> None:
        try:
            source_root = self.config.root.resolve(strict=True)
            destination_root = self.destination_root.resolve(strict=False)
        except OSError as exc:
            raise SurfAgentError(f"could not resolve cookie profile paths: {exc}") from exc
        if _contains(source_root, destination_root) or _contains(destination_root, source_root):
            raise SurfAgentError("cookie source and destination profiles must not overlap")
        if not source_db.is_file():
            raise SurfAgentError("cookie source database does not exist")
        if destination_profile.exists() and not destination_profile.is_dir():
            raise SurfAgentError("destination Default profile path is not a directory")

    def _fingerprint(self, source_db: Path, relative_candidate: Path) -> str:
        payload = {
            "source_root": str(self.config.root),
            "source_profile": self.config.profile,
            "family": self.config.family,
            "scope": self.config.scope.to_json(),
            "destination": str(self.destination_root.resolve(strict=False)),
            "candidate": str(relative_candidate),
            "files": {suffix: stat_fingerprint(Path(f"{source_db}{suffix}")) for suffix in ("", *_SIDECARS)},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _stored_fingerprint(self) -> str | None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise SurfAgentError(f"could not read cookie import state {self.state_file}: {exc}") from exc
        value = data.get("fingerprint") if isinstance(data, dict) else None
        return value if isinstance(value, str) else None

    def _write_fingerprint(self, fingerprint: str) -> None:
        write_config(self.state_file, {"fingerprint": fingerprint})

    def _backup_source(self, source_db: Path) -> Path:
        self.state_root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="cookie-source-", suffix=".sqlite", dir=self.state_root)
        os.close(descriptor)
        staging = Path(name)
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = sqlite3.connect(f"file:{quote(str(source_db))}?mode=ro", uri=True)
            target = sqlite3.connect(staging)
            source.backup(target)
            target.commit()
            return staging
        except sqlite3.Error as exc:
            _unlink_tree_file(staging)
            raise SurfAgentError("could not make a safe online cookie database backup") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
            if not staging.exists():
                _unlink_tree_file(staging)

    def _merge_existing_destination(self, staging: Path, destination_db: Path) -> int:
        validate_cookie_database(staging)
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(destination_db)
            source_columns = cookie_columns(staging)
            destination_columns = cookie_columns_connection(destination)
            if source_columns != destination_columns:
                raise SurfAgentError("source and destination cookie schemas are incompatible")
            source_key = usable_unique_index(staging, source_columns)
            destination_key = usable_unique_index_connection(destination, destination_columns)
            if source_key != destination_key:
                raise SurfAgentError("source and destination cookie identities are incompatible")
            selected = list(iter_scoped_rows(staging, source_columns, self.config))
            if not selected:
                return 0
            columns_sql = ", ".join(identifier(column) for column in source_columns)
            placeholders = ", ".join("?" for _ in source_columns)
            key_sql = ", ".join(identifier(column) for column in destination_key)
            updates = [column for column in source_columns if column not in destination_key]
            if not updates:
                raise SurfAgentError("cookie schema has no non-key columns to update")
            update_sql = ", ".join(f"{identifier(column)}=excluded.{identifier(column)}" for column in updates)
            statement = f"INSERT INTO cookies ({columns_sql}) VALUES ({placeholders}) ON CONFLICT ({key_sql}) DO UPDATE SET {update_sql}"
            self._ensure_destination_inactive()
            destination.execute("BEGIN IMMEDIATE")
            try:
                destination.executemany(statement, selected)
                destination.commit()
            except (sqlite3.Error, ValueError) as exc:
                destination.rollback()
                raise SurfAgentError("cookie import failed; destination changes were rolled back") from exc
            return len(selected)
        except SurfAgentError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise SurfAgentError("could not merge cookies into the destination profile") from exc
        finally:
            if destination is not None:
                destination.close()

    def _install_new_destination(
        self,
        *,
        staging: Path,
        relative_candidate: Path,
        source_os_crypt: Any,
        destination_local_state_absent: bool,
    ) -> Path:
        destination = self.destination_root / DESTINATION_PROFILE / relative_candidate
        self._ensure_destination_inactive()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".cookie-import-", suffix=".sqlite", dir=destination.parent)
        os.close(descriptor)
        candidate = Path(name)
        local_state_identity: tuple[int, int] | None = None
        try:
            shutil.copyfile(staging, candidate)
            connection = sqlite3.connect(candidate)
            try:
                columns = cookie_columns_connection(connection)
                usable_unique_index_connection(connection, columns)
                if "host_key" not in columns:
                    raise SurfAgentError("cookie schema does not contain host_key")
                rows_to_remove = [row[0] for row in connection.execute("SELECT rowid, host_key FROM cookies") if not scope_matches(str(row[1] or ""), self.config)]
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.executemany("DELETE FROM cookies WHERE rowid=?", ((rowid,) for rowid in rows_to_remove))
                    connection.commit()
                except sqlite3.Error as exc:
                    connection.rollback()
                    raise SurfAgentError("could not filter initial destination cookies") from exc
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
            for suffix in _SIDECARS:
                if Path(f"{candidate}{suffix}").exists():
                    raise SurfAgentError("initial cookie database could not be finalized without SQLite sidecars")
            if destination_local_state_absent:
                local_state_path = self.destination_root / "Local State"
                self.publication_hook("local-state", local_state_path)
                self._ensure_destination_inactive()
                local_state_identity = _publish_new_local_state(local_state_path, source_os_crypt)
            self.publication_hook("cookies", destination)
            self._ensure_destination_inactive()
            _publish_new_database(candidate, destination)
            return destination
        except SurfAgentError:
            if local_state_identity is not None:
                _remove_created_local_state(self.destination_root / "Local State", local_state_identity)
            raise
        except (OSError, sqlite3.Error) as exc:
            if local_state_identity is not None:
                _remove_created_local_state(self.destination_root / "Local State", local_state_identity)
            raise SurfAgentError("could not initialize destination cookie database") from exc
        finally:
            _unlink_tree_file(candidate)


def discover_cookie_database(profile: Path) -> tuple[Path, Path]:
    found: list[tuple[Path, Path]] = []
    for candidate in _COOKIE_CANDIDATES:
        path = profile / candidate
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SurfAgentError("could not inspect cookie source database") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SurfAgentError("cookie source database is a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise SurfAgentError("cookie source database is not a regular file")
        found.append((path, candidate))
    if not found:
        raise SurfAgentError("cookie source profile has no Cookies database")
    if len(found) != 1:
        raise SurfAgentError("cookie source profile has ambiguous Cookies database locations")
    return found[0]

def discover_destination_cookie_database(profile: Path) -> Path | None:
    found = [profile / candidate for candidate in _COOKIE_CANDIDATES if (profile / candidate).is_file()]
    if len(found) > 1:
        raise SurfAgentError("destination profile has ambiguous Cookies database locations")
    return found[0] if found else None


def stat_fingerprint(path: Path) -> dict[str, int | bool]:
    try:
        info = path.stat()
    except FileNotFoundError:
        return {"present": False}
    except OSError as exc:
        raise SurfAgentError("could not fingerprint cookie source files") from exc
    return {"present": True, "inode": info.st_ino, "size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def read_os_crypt(path: Path, *, required: bool) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if required:
            raise SurfAgentError(f"cookie source Local State is missing: {path}") from exc
        raise AssertionError("use read_destination_os_crypt for optional destination metadata")
    except (OSError, json.JSONDecodeError) as exc:
        raise SurfAgentError(f"could not read Local State metadata: {exc}") from exc
    if not isinstance(payload, dict) or "os_crypt" not in payload or payload["os_crypt"] is None:
        raise SurfAgentError("cookie source Local State is missing required os_crypt metadata")
    return payload["os_crypt"]


def read_destination_os_crypt(path: Path) -> tuple[bool, Any | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, None
    except (OSError, json.JSONDecodeError) as exc:
        raise SurfAgentError(f"could not read Local State metadata: {exc}") from exc
    if not isinstance(payload, dict) or "os_crypt" not in payload or payload["os_crypt"] is None:
        raise SurfAgentError("destination Local State is missing required os_crypt metadata")
    return True, payload["os_crypt"]

def write_local_state_os_crypt(path: Path, os_crypt: Any) -> None:
    # New destinations receive only encryption metadata, never source preferences.
    write_config(path, {"os_crypt": os_crypt})


def validate_cookie_database(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise SurfAgentError("cookie source backup failed SQLite integrity validation")
        columns = cookie_columns_connection(connection)
        usable_unique_index_connection(connection, columns)
    except SurfAgentError:
        raise
    except sqlite3.Error as exc:
        raise SurfAgentError("cookie source backup has an invalid cookie schema") from exc
    finally:
        if connection is not None:
            connection.close()


def cookie_columns(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    try:
        return cookie_columns_connection(connection)
    finally:
        connection.close()


def cookie_columns_connection(connection: sqlite3.Connection) -> tuple[str, ...]:
    try:
        table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cookies'").fetchone()
        if table is None:
            raise SurfAgentError("cookie database has no cookies table")
        columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(cookies)") if isinstance(row[1], str))
    except sqlite3.Error as exc:
        raise SurfAgentError("could not inspect cookie database schema") from exc
    if not columns or "host_key" not in columns:
        raise SurfAgentError("cookie database has an invalid cookies table")
    return columns


def usable_unique_index(path: Path, columns: tuple[str, ...]) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    try:
        return usable_unique_index_connection(connection, columns)
    finally:
        connection.close()


def usable_unique_index_connection(connection: sqlite3.Connection, columns: tuple[str, ...]) -> tuple[str, ...]:
    candidates: list[tuple[str, ...]] = []
    try:
        for row in connection.execute("PRAGMA index_list(cookies)"):
            name, unique, partial = str(row[1]), bool(row[2]), bool(row[4])
            if not unique or partial:
                continue
            key_rows = sorted(
                (entry for entry in connection.execute(f"PRAGMA index_xinfo({identifier(name)})") if entry[5] == 1),
                key=lambda entry: int(entry[0]),
            )
            keys = tuple(str(entry[2]) for entry in key_rows if entry[1] >= 0 and isinstance(entry[2], str))
            if keys and len(keys) == len(key_rows) and all(key in columns for key in keys):
                candidates.append(keys)
    except sqlite3.Error as exc:
        raise SurfAgentError("could not inspect cookie identity indexes") from exc
    unique_candidates = list(dict.fromkeys(candidates))
    if len(unique_candidates) != 1:
        raise SurfAgentError("cookie schema has no unambiguous usable unique identity")
    return unique_candidates[0]


def iter_scoped_rows(path: Path, columns: tuple[str, ...], config: CookieSourceConfig) -> Iterable[tuple[Any, ...]]:
    connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    try:
        host_index = columns.index("host_key")
        query = f"SELECT {', '.join(identifier(column) for column in columns)} FROM cookies"
        for row in connection.execute(query):
            if scope_matches(str(row[host_index] or ""), config):
                yield tuple(row)
    except sqlite3.Error as exc:
        raise SurfAgentError("could not read staged cookie rows") from exc
    finally:
        connection.close()


def count_scoped_rows(path: Path, config: CookieSourceConfig) -> int:
    columns = cookie_columns(path)
    return sum(1 for _ in iter_scoped_rows(path, columns, config))


def scope_matches(host_key: str, config: CookieSourceConfig) -> bool:
    if config.scope.all_domains:
        return True
    host = host_key.lstrip(".").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in config.scope.domains)


def identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise SurfAgentError("cookie schema contains an invalid identifier")
    return '"' + value.replace('"', '""') + '"'


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_regular_non_symlink(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise SurfAgentError(f"{label} disappeared") from exc
    except OSError as exc:
        raise SurfAgentError(f"could not inspect {label}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SurfAgentError(f"{label} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise SurfAgentError(f"{label} is not a regular file")


def _publish_new_local_state(path: Path, os_crypt: Any) -> tuple[int, int]:
    payload = (json.dumps({"os_crypt": os_crypt}, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SurfAgentError("destination Local State already exists") from exc
    try:
        opened = os.fstat(descriptor)
        identity = opened.st_dev, opened.st_ino
    except OSError:
        os.close(descriptor)
        raise
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return identity
    except OSError:
        _remove_created_local_state(path, identity)
        raise


def _publish_new_database(candidate: Path, destination: Path) -> None:
    try:
        os.link(candidate, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise SurfAgentError("destination Cookies database already exists") from exc
    except OSError as exc:
        raise SurfAgentError("could not publish initial destination Cookies database") from exc


def _remove_created_local_state(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.stat()
        if (info.st_dev, info.st_ino) == identity:
            path.unlink()
    except (FileNotFoundError, OSError):
        pass

def _unlink_tree_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
