import os
import json
import hashlib
import tempfile
from datetime import datetime

import pytest

# Import functions and dataclass from the monitor module (assuming code is in monitor.py)
# If the file is named main.py, adjust the import accordingly.
try:
    from monitor import scan_directory, compare_snapshots, format_report_text, hash_file, generate_snapshot, FileEntry
except ImportError:
    # For the sake of this test, fallback to a local import from a script in the same directory
    from main import scan_directory, compare_snapshots, format_report_text, hash_file, generate_snapshot, FileEntry


class TestHashFile:
    def test_hash_of_known_content(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"hello world")
            f.flush()
            path = f.name
        try:
            result = hash_file(path)
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert result == expected
        finally:
            os.unlink(path)

    def test_hash_permission_denied(self, monkeypatch):
        # Simulate a PermissionError when reading the file
        def mock_read_bytes(path):
            raise PermissionError("Mocked")
        monkeypatch.setattr("pathlib.Path.read_bytes", mock_read_bytes)
        result = hash_file("dummy_path")
        assert result == "PERMISSION_DENIED"

    def test_hash_nonexistent_file(self):
        result = hash_file("/nonexistent/file.txt")
        # Should catch OSError and return PERMISSION_DENIED
        assert result == "PERMISSION_DENIED"


class TestScanDirectory:
    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        self.tmpdir.cleanup()

    def test_empty_directory(self):
        entries = scan_directory(self.tmpdir.name)
        assert isinstance(entries, dict)
        assert len(entries) == 0

    def test_directory_with_files(self):
        base = self.tmpdir.name
        # Create files
        file1 = os.path.join(base, "a.txt")
        file2 = os.path.join(base, "sub", "b.log")
        os.makedirs(os.path.dirname(file2), exist_ok=True)
        with open(file1, "wb") as f:
            f.write(b"content1")
        with open(file2, "wb") as f:
            f.write(b"content2")

        entries = scan_directory(base)
        assert len(entries) == 2
        assert "a.txt" in entries
        assert "sub" + os.sep + "b.log" in entries  # relative path with OS separator

        # Check FileEntry attributes
        entry_a = entries["a.txt"]
        assert entry_a.path == "a.txt"
        assert entry_a.size == len(b"content1")
        assert isinstance(entry_a.mtime, float)
        assert entry_a.hash == hashlib.sha256(b"content1").hexdigest()

    def test_permission_error_handling(self, monkeypatch):
        # Patch os.stat to raise PermissionError for the first call
        original_stat = os.stat
        call_count = 0

        def mock_stat(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PermissionError("test")
            return original_stat(path)

        monkeypatch.setattr(os, "stat", mock_stat)
        # Create a directory with one file
        base = self.tmpdir.name
        filepath = os.path.join(base, "blocked_file.txt")
        with open(filepath, "w") as f:
            f.write("data")

        entries = scan_directory(base)
        # The file should be skipped, so entries dict is empty
        assert len(entries) == 0  # edge case: file omitted due to permission error


class TestGenerateSnapshot:
    def test_generates_correct_snapshot(self):
        entries = {
            "file1.txt": FileEntry(path="file1.txt", size=123, mtime=1234567890.0, hash="abc123"),
            "sub/file2.txt": FileEntry(path="sub/file2.txt", size=456, mtime=1234567891.0, hash="def456"),
        }
        snap = generate_snapshot(entries)
        assert "timestamp" in snap
        assert "files" in snap
        assert len(snap["files"]) == 2
        assert snap["files"]["file1.txt"]["hash"] == "abc123"
        # Verify timestamp is a valid ISO format
        datetime.fromisoformat(snap["timestamp"])


class TestCompareSnapshots:
    def test_added_removed_modified(self):
        old_snapshot = {
            "timestamp": "2023-01-01T00:00:00",
            "files": {
                "file_a.txt": {"size": 10, "mtime": 1.0, "hash": "hashA"},
                "file_b.txt": {"size": 20, "mtime": 2.0, "hash": "hashB"},
                "unchanged.txt": {"size": 30, "mtime": 3.0, "hash": "hashC"},
            }
        }
        new_entries = {
            "file_b.txt": FileEntry(path="file_b.txt", size=20, mtime=2.1, hash="hashB_changed"),
            "unchanged.txt": FileEntry(path="unchanged.txt", size=30, mtime=4.0, hash="hashC"),
            "file_c.txt": FileEntry(path="file_c.txt", size=40, mtime=5.0, hash="hashD"),
        }
        diff = compare_snapshots(old_snapshot, new_entries)
        assert diff["added"] == ["file_c.txt"]
        assert diff["removed"] == ["file_a.txt"]
        assert len(diff["modified"]) == 1
        mod = diff["modified"][0]
        assert mod["path"] == "file_b.txt"
        assert mod["old_hash"] == "hashB" and mod["new_hash"] == "hashB_changed"
        assert diff["total_added"] == 1
        assert diff["total_removed"] == 1
        assert diff["total_modified"] == 1

    def test_no_changes(self):
        old = {
            "timestamp": "x",
            "files": {
                "file.txt": {"size": 100, "mtime": 1.0, "hash": "xxx"}
            }
        }
        new = {
            "file.txt": FileEntry(path="file.txt", size=100, mtime=2.0, hash="xxx")
        }
        diff = compare_snapshots(old, new)
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == []
        assert diff["total_added"] == diff["total_removed"] == diff["total_modified"] == 0

    def test_empty_old_snapshot(self):
        old = {"timestamp": "x", "files": {}}
        new = {
            "newfile.txt": FileEntry(path="newfile.txt", size=1, mtime=0.0, hash="yyy")
        }
        diff = compare_snapshots(old, new)
        assert diff["added"] == ["newfile.txt"]
        assert diff["removed"] == []
        assert diff["modified"] == []


class TestFormatReportText:
    def test_full_report(self):
        diff = {
            "added": ["new_file.py", "sub/another.py"],
            "removed": ["old_file.py"],
            "modified": [
                {"path": "mod.txt", "old_size": 10, "new_size": 20, "old_hash": "a", "new_hash": "b"}
            ],
            "total_added": 2,
            "total_removed": 1,
            "total_modified": 1,
        }
        report = format_report_text(diff)
        assert "DIRECTORY CHANGE REPORT" in report
        assert "[+] ADDED (2 files):" in report
        assert "    + new_file.py" in report
        assert "    + sub/another.py" in report
        assert "[-] REMOVED (1 files):" in report
        assert "    - old_file.py" in report
        assert "  mod.txt (size: 10 -> 20, hash: a -> b)" in report

    def test_no_changes_report(self):
        diff = {
            "added": [],
            "removed": [],
            "modified": [],
            "total_added": 0,
            "total_removed": 0,
            "total_modified": 0,
        }
        report = format_report_text(diff)
        assert "DIRECTORY CHANGE REPORT" in report
        assert "ADDED" not in report
        assert "REMOVED" not in report
        assert "MODIFIED" not in report
        assert "No changes detected" in report


if __name__ == "__main__":
    pytest.main([__file__])