"""Tests for the packaged long-running recode service."""

from xbox_media_utils.systemd import recode_unit_path


def test_recode_systemd_unit_is_packaged_and_safely_scoped():
    unit = recode_unit_path()
    contents = unit.read_text()

    assert unit.exists()
    assert "/mnt/jbod/plex/%i" in contents
    assert "/mnt/jbod/plex --plex" not in contents
    assert "--wait-for-gpu-idle" in contents
    assert "--wait-for-plex-idle" in contents
    assert "Restart=on-abnormal" in contents
    assert "RequiresMountsFor=/mnt/jbod/plex/%i" in contents
    assert "IOSchedulingClass=idle" in contents
    assert "IOWeight=10" in contents
