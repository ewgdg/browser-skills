from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from surf_agent.config import CookieScope, resolve_cookie_source
from surf_agent.cookie_import import CookieImporter
from surf_agent.errors import SurfAgentError


COLUMNS = "host_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT, encrypted_value BLOB, path TEXT NOT NULL, expires_utc INTEGER, is_secure INTEGER, top_frame_site_key TEXT NOT NULL DEFAULT '', source_scheme INTEGER NOT NULL DEFAULT 0"
UNIQUE = "CREATE UNIQUE INDEX cookies_unique ON cookies(host_key, name, path, top_frame_site_key, source_scheme)"


def make_profile(root: Path, *, network: bool = False) -> Path:
    profile = root / "Default"
    profile.mkdir(parents=True)
    (root / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": "test-key", "audit_enabled": False}}))
    db = profile / "Network" / "Cookies" if network else profile / "Cookies"
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db)
    connection.execute(f"CREATE TABLE cookies ({COLUMNS})")
    connection.execute(UNIQUE)
    connection.commit()
    connection.close()
    return db


def put(db: Path, host: str, name: str, value: str, *, partition: str = "") -> None:
    connection = sqlite3.connect(db)
    connection.execute(
        "INSERT INTO cookies(host_key,name,value,encrypted_value,path,expires_utc,is_secure,top_frame_site_key,source_scheme) VALUES(?,?,?,?,?,?,?,?,?)",
        (host, name, value, value.encode(), "/", 10, 1, partition, 0),
    )
    connection.commit()
    connection.close()


def rows(db: Path) -> list[tuple[str, str, str, str]]:
    connection = sqlite3.connect(db)
    result = connection.execute("SELECT host_key,name,value,top_frame_site_key FROM cookies ORDER BY host_key,name,top_frame_site_key").fetchall()
    connection.close()
    return result


def importer(tmp_path: Path, source_root: Path, destination: Path, *, domains: list[str] | None = None, all_domains: bool = False) -> CookieImporter:
    scope = CookieScope.all() if all_domains else CookieScope.from_domains(domains or ["example.com"])
    config = resolve_cookie_source(source=source_root, profile="Default", scope=scope)
    return CookieImporter(config=config, destination_root=destination, state_root=tmp_path / "state", destination_family="chrome")


def test_online_backup_reads_committed_live_wal_source_and_merges_scoped_rows(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    live = sqlite3.connect(source)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO cookies(host_key,name,value,encrypted_value,path,expires_utc,is_secure,top_frame_site_key,source_scheme) VALUES(?,?,?,?,?,?,?,?,?)", (".example.com", "session", "secret-source", b"secret-source", "/", 10, 1, "", 0))
    live.commit()
    destination = tmp_path / "destination"
    result = importer(tmp_path, source_root, destination).run(force=True)
    live.close()

    assert result.imported_rows == 1
    assert rows(destination / "Default" / "Cookies") == [(".example.com", "session", "secret-source", "")]


def test_domain_scope_matches_parent_and_subdomains_not_suffix_lookalikes(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    for host in ("example.com", ".example.com", "api.example.com", "badexample.com"):
        put(source, host, host, "secret")
    destination = tmp_path / "destination"

    importer(tmp_path, source_root, destination).run(force=True)

    assert [row[0] for row in rows(destination / "Default" / "Cookies")] == [".example.com", "api.example.com", "example.com"]


def test_upsert_preserves_destination_only_rows_and_partition_columns(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "same", "new", partition="https://top.example")
    destination = tmp_path / "destination"
    dest = make_profile(destination)
    put(dest, ".example.com", "same", "old", partition="https://top.example")
    put(dest, ".other.test", "keep", "destination")

    importer(tmp_path, source_root, destination).run(force=True)

    assert rows(dest) == [(".example.com", "same", "new", "https://top.example"), (".other.test", "keep", "destination", "")]


def test_all_domains_is_still_upsert_only(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".outside.test", "source", "new")
    destination = tmp_path / "destination"
    dest = make_profile(destination)
    put(dest, ".outside.test", "only-destination", "keep")

    importer(tmp_path, source_root, destination, all_domains=True).run(force=True)

    assert rows(dest) == [(".outside.test", "only-destination", "keep", ""), (".outside.test", "source", "new", "")]


def test_destination_schema_error_rolls_back_without_leaking_cookie_value(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "new", "never-print")
    destination = tmp_path / "destination"
    dest = make_profile(destination)
    put(dest, ".example.com", "old", "old-value")
    connection = sqlite3.connect(dest)
    connection.execute("DROP INDEX cookies_unique")
    connection.commit()
    connection.close()

    with pytest.raises(SurfAgentError) as caught:
        importer(tmp_path, source_root, destination).run(force=True)

    assert "never-print" not in str(caught.value)
    assert rows(dest) == [(".example.com", "old", "old-value", "")]


def test_candidate_paths_and_metadata_mismatch_fail_closed(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root, network=True)
    destination = tmp_path / "destination"
    (destination / "Default").mkdir(parents=True)
    (destination / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": "different"}}))

    with pytest.raises(SurfAgentError, match="encryption metadata"):
        importer(tmp_path, source_root, destination).run(force=True)

    # Two valid candidates are ambiguous rather than guessed.
    shutil.copyfile(source, source_root / "Default" / "Cookies")
    with pytest.raises(SurfAgentError, match="ambiguous"):
        importer(tmp_path, source_root, tmp_path / "new-destination").run(force=True)


def test_unchanged_automatic_import_skips_without_opening_destination_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "value")
    destination = tmp_path / "destination"
    service = importer(tmp_path, source_root, destination)
    service.run(force=True)

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise AssertionError("destination SQLite should not open")

    monkeypatch.setattr("surf_agent.cookie_import.sqlite3.connect", fail_connect)
    result = service.run(force=False)
    assert result.skipped is True


def test_mismatched_unique_identity_rejects_partitioned_source_without_mutating_destination(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "partition-a", partition="https://a.example")
    put(source, ".example.com", "session", "partition-b", partition="https://b.example")
    destination = tmp_path / "destination"
    dest = make_profile(destination)
    put(dest, ".example.com", "existing", "keep")
    connection = sqlite3.connect(dest)
    connection.execute("DROP INDEX cookies_unique")
    connection.execute("CREATE UNIQUE INDEX cookies_unique ON cookies(host_key, name, path)")
    connection.commit()
    connection.close()

    with pytest.raises(SurfAgentError, match="identit"):
        importer(tmp_path, source_root, destination).run(force=True)

    assert rows(dest) == [(".example.com", "existing", "keep", "")]


def test_matching_partitioned_identity_preserves_each_selected_source_row(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "partition-a", partition="https://a.example")
    put(source, ".example.com", "session", "partition-b", partition="https://b.example")
    destination = tmp_path / "destination"
    dest = make_profile(destination)

    importer(tmp_path, source_root, destination).run(force=True)

    assert rows(dest) == [
        (".example.com", "session", "partition-a", "https://a.example"),
        (".example.com", "session", "partition-b", "https://b.example"),
    ]


def test_existing_local_state_missing_or_null_os_crypt_fails_without_overwrite(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    for metadata in ({"other": "keep"}, {"os_crypt": None, "other": "keep"}):
        destination = tmp_path / f"destination-{len(metadata)}-{str(metadata.get('os_crypt'))}"
        local_state = destination / "Local State"
        local_state.parent.mkdir(parents=True)
        original = json.dumps(metadata, sort_keys=True)
        local_state.write_text(original)
        with pytest.raises(SurfAgentError, match="os_crypt"):
            importer(tmp_path, source_root, destination).run(force=True)
        assert local_state.read_text() == original
        assert not (destination / "Default" / "Cookies").exists()


def test_equal_existing_local_state_preserves_unrelated_fields(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    destination.mkdir()
    metadata = {"os_crypt": {"encrypted_key": "test-key", "audit_enabled": False}, "unrelated": {"keep": True}}
    local_state = destination / "Local State"
    original = json.dumps(metadata, sort_keys=True)
    local_state.write_text(original)

    importer(tmp_path, source_root, destination).run(force=True)

    assert json.loads(local_state.read_text()) == metadata


def test_existing_malformed_local_state_fails_without_overwrite(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination-malformed"
    destination.mkdir()
    local_state = destination / "Local State"
    original = "{malformed"
    local_state.write_text(original)

    with pytest.raises(SurfAgentError, match="Local State"):
        importer(tmp_path, source_root, destination).run(force=True)

    assert local_state.read_text() == original
    assert not (destination / "Default" / "Cookies").exists()


def test_non_linux_overlap_and_owner_validation_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    service = importer(tmp_path, source_root, tmp_path / "destination")
    service.platform_name = "darwin"
    with pytest.raises(SurfAgentError, match="Linux"):
        service.run(force=True)

    with pytest.raises(SurfAgentError, match="overlap"):
        importer(tmp_path, source_root, source_root).run(force=True)

    monkeypatch.setattr("surf_agent.cookie_import.os.getuid", lambda: source_root.stat().st_uid + 1)
    with pytest.raises(SurfAgentError, match="owned"):
        importer(tmp_path, source_root, tmp_path / "other-destination").run(force=True)


def test_fingerprint_changes_make_automatic_import_run_for_db_sidecar_scope_and_destination(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "one")
    destination = tmp_path / "destination"
    first = importer(tmp_path, source_root, destination)
    first.run(force=True)
    assert first.run(force=False).skipped is True

    connection = sqlite3.connect(source)
    connection.execute("UPDATE cookies SET value=?, encrypted_value=? WHERE host_key=? AND name=?", ("two", b"two", ".example.com", "session"))
    connection.commit()
    connection.close()
    assert first.run(force=False).skipped is False
    assert first.run(force=False).skipped is True

    sidecar = Path(f"{source}-wal")
    baseline = first._fingerprint(source, Path("Cookies"))
    sidecar.write_bytes(b"wal")
    with_sidecar = first._fingerprint(source, Path("Cookies"))
    os.utime(sidecar, None)
    changed_sidecar_metadata = first._fingerprint(source, Path("Cookies"))
    sidecar.unlink()
    assert baseline != with_sidecar
    assert with_sidecar != changed_sidecar_metadata

    changed_scope = importer(tmp_path, source_root, destination, all_domains=True)
    assert changed_scope.run(force=False).skipped is False
    changed_destination = importer(tmp_path, source_root, tmp_path / "other-destination", all_domains=True)
    assert changed_destination.run(force=False).skipped is False


def test_source_change_during_backup_retries_then_exhaustion_preserves_destination_and_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    dest = make_profile(destination)
    put(dest, ".example.com", "existing", "keep")
    service = importer(tmp_path, source_root, destination)
    original_rows = rows(dest)
    original_fingerprint = service.state_file.read_text() if service.state_file.exists() else None
    original_fingerprint_fn = service._fingerprint
    calls = 0

    def changing_fingerprint(db: Path, candidate: Path) -> str:
        nonlocal calls
        calls += 1
        return f"fingerprint-{calls}"

    monkeypatch.setattr(service, "_fingerprint", changing_fingerprint)
    with pytest.raises(SurfAgentError, match="changed while"):
        service.run(force=True)

    assert rows(dest) == original_rows
    assert (service.state_file.read_text() if service.state_file.exists() else None) == original_fingerprint
    assert not list((tmp_path / "state").glob("cookie-source-*.sqlite"))
    assert original_fingerprint_fn(source, Path("Cookies"))


def test_external_destination_activation_after_backup_prevents_all_mutation(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    states = iter([False, True])
    service = importer(tmp_path, source_root, destination)
    service.process_inspector = lambda _path: next(states)

    with pytest.raises(SurfAgentError, match="active"):
        service.run(force=True)

    assert not (destination / "Local State").exists()
    assert not (destination / "Default" / "Cookies").exists()
    assert not service.state_file.exists()


def test_destination_owner_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    actual_stat = Path.stat
    source_uid = actual_stat(source_root).st_uid

    def mismatched_stat(path: Path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if path == tmp_path:
            return type("Stat", (), {"st_uid": source_uid + 1})()
        return result

    monkeypatch.setattr("surf_agent.cookie_import.Path.stat", mismatched_stat)
    with pytest.raises(SurfAgentError, match="different owners"):
        importer(tmp_path, source_root, destination).run(force=True)


def test_profile_identity_change_invalidates_automatic_fingerprint(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    other_profile = source_root / "Profile 1"
    other_profile.mkdir()
    other = other_profile / "Cookies"
    shutil.copyfile(source, other)
    destination = tmp_path / "destination"
    original = importer(tmp_path, source_root, destination)
    original.run(force=True)
    assert original.run(force=False).skipped is True

    other_config = resolve_cookie_source(source=source_root, profile="Profile 1", scope=CookieScope.from_domains(["example.com"]))
    changed_profile = CookieImporter(config=other_config, destination_root=destination, state_root=tmp_path / "state", destination_family="chrome")
    assert changed_profile.run(force=False).skipped is False


def test_new_destination_publish_never_overwrites_concurrent_local_state(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    service = importer(tmp_path, source_root, destination)
    concurrent = b'{"concurrent":"local-state"}'

    def publish_hook(boundary: str, _path: Path) -> None:
        if boundary == "local-state":
            destination.mkdir(exist_ok=True)
            (destination / "Local State").write_bytes(concurrent)

    service.publication_hook = publish_hook
    with pytest.raises(SurfAgentError, match="already exists"):
        service.run(force=True)

    assert (destination / "Local State").read_bytes() == concurrent
    assert not (destination / "Default" / "Cookies").exists()
    assert not service.state_file.exists()
    assert not list((destination / "Default").glob(".cookie-import-*.sqlite"))


def test_new_destination_publish_never_overwrites_concurrent_cookies_and_rolls_back_own_local_state(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    service = importer(tmp_path, source_root, destination)
    concurrent = b"concurrent-cookies"

    def publish_hook(boundary: str, _path: Path) -> None:
        if boundary == "cookies":
            cookie_path = destination / "Default" / "Cookies"
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            cookie_path.write_bytes(concurrent)

    service.publication_hook = publish_hook
    with pytest.raises(SurfAgentError, match="already exists"):
        service.run(force=True)

    assert (destination / "Default" / "Cookies").read_bytes() == concurrent
    assert not (destination / "Local State").exists()
    assert not service.state_file.exists()
    assert not list((destination / "Default").glob(".cookie-import-*.sqlite"))


def test_runtime_source_root_and_cookie_symlinks_are_rejected_without_destination_mutation(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "source")
    destination = tmp_path / "destination"
    service = importer(tmp_path, source_root, destination)
    moved_root = tmp_path / "real-google-chrome"
    source_root.rename(moved_root)
    source_root.symlink_to(moved_root, target_is_directory=True)

    with pytest.raises(SurfAgentError, match="source root.*symlink"):
        service.run(force=True)
    assert not (destination / "Default" / "Cookies").exists()

    source_root.unlink()
    moved_root.rename(source_root)
    moved_db = source_root / "Default" / "Cookies.real"
    source = source_root / "Default" / "Cookies"
    source.rename(moved_db)
    source.symlink_to(moved_db)
    service = importer(tmp_path, source_root, destination)
    with pytest.raises(SurfAgentError, match="cookie source.*symlink"):
        service.run(force=True)
    assert not (destination / "Default" / "Cookies").exists()


def test_journal_sidecar_presence_and_metadata_invalidate_fingerprint(tmp_path: Path) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    service = importer(tmp_path, source_root, tmp_path / "destination")
    baseline = service._fingerprint(source, Path("Cookies"))
    journal = Path(f"{source}-journal")
    journal.write_bytes(b"journal")
    present = service._fingerprint(source, Path("Cookies"))
    os.utime(journal, None)
    changed = service._fingerprint(source, Path("Cookies"))
    journal.unlink()
    removed = service._fingerprint(source, Path("Cookies"))

    assert baseline != present
    assert present != changed
    assert removed == baseline


def test_one_unstable_backup_is_discarded_then_stable_snapshot_commits_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "google-chrome"
    source = make_profile(source_root)
    put(source, ".example.com", "session", "old")
    destination = tmp_path / "destination"
    service = importer(tmp_path, source_root, destination)
    original_backup = service._backup_source
    backups = 0

    def backup_then_stabilize(db: Path) -> Path:
        nonlocal backups
        backups += 1
        staging = original_backup(db)
        if backups == 1:
            connection = sqlite3.connect(source)
            connection.execute("UPDATE cookies SET value=?, encrypted_value=? WHERE host_key=? AND name=?", ("stable", b"stable", ".example.com", "session"))
            connection.commit()
            connection.close()
        return staging

    fingerprints = iter(["initial", "attempt-one-before", "attempt-one-after", "stable", "stable", "stable"])
    monkeypatch.setattr(service, "_backup_source", backup_then_stabilize)
    monkeypatch.setattr(service, "_fingerprint", lambda _db, _candidate: next(fingerprints))

    service.run(force=True)

    assert backups == 2
    assert rows(destination / "Default" / "Cookies") == [(".example.com", "session", "stable", "")]
    assert json.loads(service.state_file.read_text()) == {"fingerprint": "stable"}


def test_local_state_publish_cleanup_preserves_concurrent_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from surf_agent.cookie_import import _publish_new_local_state

    path = tmp_path / "destination" / "Local State"
    replacement = b'{"concurrent":"replacement"}'

    def replace_then_fail(_descriptor: int) -> None:
        path.unlink()
        path.write_bytes(replacement)
        raise OSError("fsync failed")

    monkeypatch.setattr("surf_agent.cookie_import.os.fsync", replace_then_fail)
    with pytest.raises(OSError, match="fsync failed"):
        _publish_new_local_state(path, {"encrypted_key": "source"})

    assert path.read_bytes() == replacement
