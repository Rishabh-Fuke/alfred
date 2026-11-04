"""Folder A is granted and folder B beside it never is. Whatever the model asks for, and whatever changes on disk
between a listing and a read, nothing in B is reached. Design test 2 and threat T7 in docs/THREAT_MODEL.md."""
import os
import stat

import pytest


def test_links_pipes_and_hidden_files_are_not_listed(session, secrets_of):
    _, secret = secrets_of
    a, b = session.a, session.b
    os.symlink(b / "secret.txt", a / "shortcut.txt")
    os.symlink(b, a / "linked folder")
    os.symlink("../B/secret.txt", a / "relative.txt")
    os.link(b / "secret.txt", a / "hardlinked.txt")
    os.mkfifo(a / "pipe")
    (a / ".env").write_text("API_KEY=" + secret)
    (a / ".git").mkdir()
    r = session.call("files__list")
    assert {e["name"] for e in r.data["entries"]} == {"CS101", "notes.txt", "syllabus.txt"}
    assert r.data["skipped"] == 5  # the links and the pipe; hidden entries aren't counted
    assert secret not in r.for_model()


def test_a_folder_swapped_for_a_link_after_listing_is_refused(session, secrets_of):
    _, secret = secrets_of
    cs101 = session.list()["CS101"]["handle"]
    hw1 = session.list(cs101)["hw1.txt"]["handle"]
    os.rename(session.a / "CS101", session.a.parent / "elsewhere")
    os.symlink(session.b, session.a / "CS101")  # B has a hw1.txt too
    for r in (session.read(hw1), session.call("files__list", folder=cs101)):
        assert (r.status, r.reason) == ("denied", "symlink")
        assert secret not in r.for_model()


def test_a_file_swapped_for_a_link_after_listing_is_refused(session, secrets_of):
    _, secret = secrets_of
    handle = session.list()["syllabus.txt"]["handle"]
    (session.a / "syllabus.txt").unlink()
    os.symlink(session.b / "secret.txt", session.a / "syllabus.txt")
    r = session.read(handle)
    assert (r.status, r.reason) == ("denied", "symlink") and secret not in r.for_model()
    assert "syllabus.txt" not in session.list()  # listing again doesn't make the link readable


def test_an_old_handle_doesnt_follow_a_replaced_file(session):
    handle = session.list()["syllabus.txt"]["handle"]
    os.replace(session.b / "secret.txt", session.a / "syllabus.txt")
    assert session.read(handle).reason == "changed"


def test_a_folder_moved_out_of_the_grant_is_out_of_reach(session):
    cs101 = session.list()["CS101"]["handle"]
    hw1 = session.list(cs101)["hw1.txt"]["handle"]
    os.rename(session.a / "CS101", session.b / "CS101")
    for r in (session.read(hw1), session.call("files__list", folder=cs101)):
        assert (r.status, r.reason) == ("denied", "changed")


def test_a_file_that_gains_a_hard_link_is_refused(session):
    handle = session.list()["notes.txt"]["handle"]
    os.link(session.a / "notes.txt", session.b / "copy.txt")  # now it also has a name outside the grant
    r = session.read(handle)
    assert (r.status, r.reason, r.bytes_returned) == ("denied", "hard_link", 0)


def _folder_with_a_mount_point():
    """A folder holding another device's mount point: /System/Volumes on macOS, /dev on Linux."""
    for parent in ("/System/Volumes", "/dev", "/run"):
        try:
            device, names = os.stat(parent).st_dev, os.listdir(parent)
        except OSError:
            continue
        for name in names:
            try:
                st = os.stat(os.path.join(parent, name), follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode) and st.st_dev != device and not name.startswith("."):
                return parent, name
    return None


def test_entries_on_another_device_are_not_listed(broker):
    found = _folder_with_a_mount_point()
    if found is None:
        pytest.skip("no folder here holds a mount point")
    parent, mounted = found
    task = broker.start_task()
    broker.grant(task, parent, origin="cli")
    r = broker.call_tool(task, "files__list", {})
    assert r.status == "ok" and r.data["skipped"] >= 1
    assert mounted not in {e["name"] for e in r.data["entries"]}


def test_a_folder_that_becomes_a_mount_point_is_refused(session, monkeypatch):
    cs101 = session.list()["CS101"]["handle"]
    hw1 = session.list(cs101)["hw1.txt"]["handle"]
    mounted = os.stat(session.a / "CS101").st_ino
    real_fstat = os.fstat

    def fstat(fd):  # CS101 now reports another device, as a mount point would
        st = real_fstat(fd)
        if st.st_ino != mounted:
            return st
        fields = list(st[:10])
        fields[2] = st.st_dev + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", fstat)
    for r in (session.read(hw1), session.call("files__list", folder=cs101)):
        assert (r.status, r.reason) == ("denied", "other_device")
