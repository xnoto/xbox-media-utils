"""Tests for the packaged long-running recode service."""

from xbox_media_utils.systemd import recode_unit_path


def test_recode_systemd_unit_is_packaged_and_safely_scoped():
    unit = recode_unit_path()
    contents = unit.read_text()

    assert unit.exists()
    assert "${XBOX_PLEX_ROOT}/%i" in contents
    assert "EnvironmentFile=-/etc/sysconfig/xbox-recode" in contents
    assert "/mnt/jbod" not in contents
    assert "--wait-for-gpu-idle" in contents
    assert "--wait-for-plex-idle" in contents
    assert "Restart=on-abnormal" in contents
    assert "After=local-fs.target network-online.target" in contents
    assert "IOSchedulingClass=idle" in contents
    assert "IOWeight=10" in contents
