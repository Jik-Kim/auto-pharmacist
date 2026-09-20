import pytest

from gmp_skills.core.rg2_status import status_fields


def _status(**overrides):
    status = {
        "offset": 0.0,
        "relative_width": 0.0,
        "width": 0.0,
        "busy": 0,
        "grip": 0,
        "s1_p": 0,
        "s1_t": 0,
        "s2_p": 0,
        "s2_t": 0,
        "safety": 0,
    }
    status.update(overrides)
    return status


def test_status_fields_map_modbus_status_and_width_definitions():
    fields = status_fields({
        "offset": 5.0,
        "relative_width": 62.9,
        "width": 64.9,
        "busy": 62,
        "grip": 1,
        "s1_p": 0,
        "s1_t": 1,
        "s2_p": 0,
        "s2_t": 1,
        "safety": 1,
    })
    assert fields == {"gfof": 50, "ggwd": 629, "gsta": 62, "gwdf": 649}


def test_raw_gsta_word_is_used_and_adjacent_legacy_keys_are_ignored():
    fields = status_fields(_status(busy=0, grip=1, s1_p=1, s1_t=1,
                                   s2_p=1, s2_t=1, safety=1))
    assert fields["gsta"] == 0
    assert status_fields(_status(busy=2))["gsta"] == 2
    assert status_fields(_status(busy=8))["gsta"] == 8


def test_negative_offset_uses_uint16_twos_complement():
    assert status_fields(_status(offset=-1.5))["gfof"] == 0xFFF1
    assert status_fields(_status(offset=6553.5))["gfof"] == 0xFFFF


def test_missing_required_status_fields_fail_closed():
    with pytest.raises(KeyError):
        status_fields({})


def test_out_of_range_signed_offset_is_rejected():
    with pytest.raises(ValueError):
        status_fields({"offset": 7000.0, "relative_width": 0, "width": 0,
                       "busy": 0, "grip": 0, "s1_p": 0, "s1_t": 0,
                       "s2_p": 0, "s2_t": 0, "safety": 0})
