import config
import history


def test_log_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("Soup", "soup", "https://x.test/soup", "http://m/g/home/r/soup")
    items = history.list_imports()
    assert len(items) == 1
    row = items[0]
    assert row["name"] == "Soup"
    assert row["slug"] == "soup"
    assert row["source_url"] == "https://x.test/soup"
    assert row["mealie_url"] == "http://m/g/home/r/soup"
    assert row["status"] == "success"
    assert row["created_at"]  # ISO timestamp present


def test_list_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    for n in ("A", "B", "C"):
        history.log_import(n, n.lower())
    assert [r["name"] for r in history.list_imports()] == ["C", "B", "A"]


def test_find_recent_by_source_normalises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("Cake", "cake", "https://x.test/cake/")  # stored without trailing /
    # trailing slash + surrounding spaces normalise to the same key
    assert history.find_recent_by_source("  https://x.test/cake  ")["slug"] == "cake"
    assert history.find_recent_by_source("https://x.test/cake/")["slug"] == "cake"
    # unknown / blank / None never match (image & text imports aren't deduped)
    assert history.find_recent_by_source("https://x.test/other") is None
    assert history.find_recent_by_source("") is None
    assert history.find_recent_by_source(None) is None


def test_find_returns_most_recent_for_same_source(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("Old name", "soup", "https://x.test/soup")
    history.log_import("New name", "soup-2", "https://x.test/soup")
    assert history.find_recent_by_source("https://x.test/soup")["name"] == "New name"


def test_discard_payload_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    rec = {"name": "Half-edited", "ingredients": [{"food": "egg", "quantity": ""}]}
    history.log_import("Half-edited", "", "https://x.test/h", "", status="discarded", payload=rec)
    row = history.list_imports()[0]
    assert row["status"] == "discarded"
    assert "id" in row
    got = history.get_import(row["id"])
    assert got["payload"]["name"] == "Half-edited"
    assert got["payload"]["ingredients"][0]["food"] == "egg"      # stored verbatim
    assert got["payload"]["ingredients"][0]["quantity"] == ""     # free-form (not coerced)
    assert history.get_import(999999) is None                     # unknown id


def test_discarded_not_counted_as_already_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("Tossed", "", "https://x.test/keep", "", status="discarded", payload={"name": "Tossed"})
    assert history.find_recent_by_source("https://x.test/keep") is None  # discards don't dedupe


# ── per-user scoping (v0.15.0) ──────────────────────────────────────────

def test_list_imports_scoped_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("A", "a", user="alice")
    history.log_import("B", "b", user="bob")
    history.log_import("A2", "a2", user="alice")
    assert [r["name"] for r in history.list_imports(user="alice")] == ["A2", "A"]
    assert [r["name"] for r in history.list_imports(user="bob")] == ["B"]
    # user=None returns everything (open / first-run mode)
    assert len(history.list_imports()) == 3


def test_get_import_is_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("A", "a", user="alice")
    row_id = history.list_imports(user="alice")[0]["id"]
    assert history.get_import(row_id, user="alice") is not None       # owner
    assert history.get_import(row_id, user="bob") is None             # not the owner
    assert history.get_import(row_id) is not None                     # user=None = open mode


def test_find_recent_by_source_scoped_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    history.log_import("Alice's cake", "cake", "https://x.test/cake", user="alice")
    # same URL, different user → doesn't show as "already imported" for alice
    history.log_import("Bob's cake", "cake-b", "https://x.test/cake", user="bob")
    assert history.find_recent_by_source("https://x.test/cake", user="alice")["slug"] == "cake"
    assert history.find_recent_by_source("https://x.test/cake", user="bob")["slug"] == "cake-b"
