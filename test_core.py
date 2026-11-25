"""Offline safety tests: python3 -m unittest -v test_core.py"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace as N
from unittest.mock import patch
from core import apply, approved_visible, light_info, read_config, temp_range, write_config


class SettingsTests(unittest.TestCase):
    def test_legacy_icon_preference_is_discarded_when_saving_settings(self):
        saved = {
            "ip": "192.168.1.20",
            "token": "test-token",
            "approved": {"bulb": "room"},
            "approved_sensors": {"sensor": "room"},
            "tray_room": "room",
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(dict(saved, tray_icon="Network")), encoding="utf-8")
            with patch("core.config_path", return_value=path):
                config = read_config()
                self.assertEqual(config, saved)
                write_config(config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)


class FakeLight:
    def __init__(self, ident, room):
        self.id = ident
        self.room = N(id=room, name=room)
        self.attributes = N(custom_name=ident, is_on=False, light_level=40,
                            color_temperature=3000, color_temperature_min=454,
                            color_temperature_max=153, color_hue=12, color_saturation=.5)
        self.capabilities = N(can_receive=["isOn", "lightLevel", "colorHue", "colorSaturation", "colorTemperature"])
        self.is_reachable = True
        self.changed = []
    def set_light(self, lamp_on): self.changed.append(("power", lamp_on))
    def set_light_level(self, light_level): self.changed.append(("brightness", light_level))
    def set_color_temperature(self, color_temp): self.changed.append(("temperature", color_temp))
    def set_light_color(self, hue, saturation): self.changed.append(("color", hue, saturation))

class Hub:
    def __init__(self, *devices): self.devices = {d.id: d for d in devices}
    def get_light_by_id(self, id_): return self.devices[id_]

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.mine = FakeLight("mine", "bedroom")
        self.other = FakeLight("someone-else", "bedroom")
        self.kitchen = FakeLight("kitchen", "kitchen")
        self.hub = Hub(self.mine, self.other, self.kitchen)
        self.allow = {"mine": "bedroom"}

    def test_unknown_device_denied_before_any_write(self):
        with self.assertRaises(PermissionError):
            apply(self.hub, self.allow, ["mine", "someone-else"], "power", False)
        self.assertEqual(self.mine.changed, [])
        self.assertEqual(self.other.changed, [])

    def test_moved_device_denied(self):
        self.mine.room.id = "kitchen"
        with self.assertRaises(PermissionError):
            apply(self.hub, self.allow, ["mine"], "power", True)
        self.assertEqual(self.mine.changed, [])

    def test_same_room_unapproved_not_visible(self):
        visible = approved_visible([light_info(self.mine),light_info(self.other)], self.allow)
        self.assertEqual([l.id for l in visible], ["mine"])

    def test_authorized_works(self):
        self.assertEqual(apply(self.hub, self.allow, ["mine"], "brightness", 55), 1)
        self.assertEqual(self.mine.changed, [("brightness", 55)])
        self.assertEqual(self.other.changed, [])

    def test_new_device_stays_unapproved(self):
        new = FakeLight("newly-added", "bedroom")
        self.hub.devices[new.id] = new
        with self.assertRaises(PermissionError):
            apply(self.hub, self.allow, ["newly-added"], "power", True)
        self.assertEqual(new.changed, [])

    def test_offline_device_stays_unchanged(self):
        self.mine.is_reachable = False
        with self.assertRaises(ValueError):
            apply(self.hub, self.allow, ["mine"], "power", True)
        self.assertEqual(self.mine.changed, [])

    def test_temperature_rejects_unsupported_value(self):
        with self.assertRaises(ValueError):
            apply(self.hub, self.allow, ["mine"], "temperature", 8000)
        self.assertEqual(self.mine.changed, [])

    def test_kelvin_mireds(self):
        self.assertEqual(temp_range(self.mine.attributes), (2203, 6536))

    def test_unsupported_group_action_is_preflighted(self):
        self.other.capabilities.can_receive.remove("colorHue")
        with self.assertRaises(ValueError):
            apply(self.hub, {"mine": "bedroom", "someone-else": "bedroom"},
                  ["mine", "someone-else"], "color", (180, .5))
        self.assertEqual(self.mine.changed, [])



class SensorTests(unittest.TestCase):
    def setUp(self):
        from core import environment_info
        self.environment_info = environment_info
        self.temp = N(id="temp1", room=N(id="mine", name="Bedroom"), is_reachable=True,
                      attributes=N(custom_name="Timmerflotte temperature", current_temperature=23.5,
                                   current_r_h=None, battery_percentage=91))
        self.rh = N(id="rh1", room=N(id="mine", name="Bedroom"), is_reachable=True,
                    attributes=N(custom_name="Timmerflotte humidity", current_temperature=None,
                                 current_r_h=52, battery_percentage=None))

    def test_split_matter_sensor_readings(self):
        t = self.environment_info(self.temp)
        h = self.environment_info(self.rh)
        self.assertEqual(t.temperature, 23.5)
        self.assertIsNone(t.humidity)
        self.assertEqual(h.humidity, 52)
        self.assertIsNone(h.temperature)

    def test_sensors_require_separate_approval(self):
        seen = [self.environment_info(self.temp), self.environment_info(self.rh)]
        self.assertEqual(approved_visible(seen, {}), [])
        self.assertEqual([s.id for s in approved_visible(seen, {"temp1": "mine"})], ["temp1"])

    def test_sensor_room_move_disables_readout(self):
        info = self.environment_info(self.temp)
        self.assertEqual(approved_visible([info], {"temp1": "former-room"}), [])

    def test_sensor_invalid_readings_do_not_crash(self):
        self.temp.attributes.current_temperature = "NaN"
        self.temp.attributes.current_r_h = "unknown"
        info = self.environment_info(self.temp)
        self.assertIsNone(info.temperature)
        self.assertIsNone(info.humidity)


class TraySafetyTests(unittest.TestCase):
    def setUp(self):
        from core import tray_room, tray_targets, stepped_brightness
        self.tray_room, self.tray_targets, self.stepped = tray_room, tray_targets, stepped_brightness
        self.me1 = FakeLight('mine1', 'myroom')
        self.me1.attributes.is_on = True
        self.me1.attributes.light_level = 40
        self.me2 = FakeLight('mine2', 'myroom')
        self.me2.attributes.is_on = True
        self.me2.attributes.light_level = 60
        self.other = FakeLight('housemate', 'myroom')
        self.other.attributes.is_on = True
        self.other_room = FakeLight('guest', 'guestroom')
        self.other_room.attributes.is_on = True
        self.allow = {'mine1': 'myroom', 'mine2': 'myroom'}

    def test_tray_only_targets_approved_room_ids(self):
        group = [light_info(x) for x in [self.me1, self.me2, self.other, self.other_room]]
        self.assertEqual([x.id for x in self.tray_targets(group, self.allow)], ['mine1', 'mine2'])

    def test_tray_excludes_switched_off_bulbs(self):
        self.me2.attributes.is_on = False
        group = [light_info(x) for x in [self.me1, self.me2, self.other]]
        self.assertEqual([x.id for x in self.tray_targets(group, self.allow)], ['mine1'])

    def test_tray_no_implicit_multi_room_control(self):
        self.allow['guest'] = 'guestroom'
        group = [light_info(x) for x in [self.me1, self.me2, self.other_room]]
        self.assertIsNone(self.tray_room(group, self.allow))
        self.assertEqual(self.tray_targets(group, self.allow), [])
        self.assertEqual([x.id for x in self.tray_targets(group, self.allow, 'myroom')], ['mine1', 'mine2'])

    def test_tray_no_fallback_if_saved_room_missing(self):
        group = [light_info(self.me1)]
        self.assertEqual(self.tray_targets(group, self.allow, 'former-room'), [])

    def test_tray_no_new_bulbs_just_because_room_matches(self):
        new = FakeLight('new', 'myroom')
        new.attributes.is_on = True
        group = [light_info(x) for x in [self.me1, self.me2, new]]
        self.assertEqual([x.id for x in self.tray_targets(group, self.allow)], ['mine1', 'mine2'])

    def test_tray_zero_visible_when_approved_light_moves(self):
        self.me1.room.id = 'guestroom'
        group = [light_info(x) for x in [self.me1, self.me2]]
        self.assertEqual([x.id for x in self.tray_targets(group, self.allow)], ['mine2'])

    def test_tray_step_brightness_and_boundaries(self):
        self.assertEqual(self.stepped([40, 60], 1), 55)
        self.assertEqual(self.stepped([40, 60], -1), 45)
        self.assertEqual(self.stepped([100], 20), 100)
        self.assertEqual(self.stepped([1], -20), 1)

    def test_tray_never_controls_unauthorized_even_if_hub_reassigned(self):
        group = [light_info(x) for x in [self.me1, self.me2, self.other]]
        target_ids = [x.id for x in self.tray_targets(group, self.allow)]
        hub = Hub(self.me1, self.me2, self.other)
        apply(hub, self.allow, target_ids, 'brightness', 55)
        self.assertEqual(self.other.changed, [])
        self.assertEqual(self.me1.changed, [('brightness', 55)])
        self.assertEqual(self.me2.changed, [('brightness', 55)])

class TimmerflotteLinkTests(unittest.TestCase):
    def fake(self, id_, room, rel, temperature=None, humidity=None, name="TIMMERFLOTTE"):
        from core import EnvironmentInfo
        return EnvironmentInfo(id_, name, room, "Bedroom" if room == 'mine' else "Unassigned",
                               True, temperature, humidity, 95, rel)

    def test_unassigned_humidity_linked_by_relation_id(self):
        from core import approved_climate, UNASSIGNED
        t = self.fake('temp', 'mine', 'physical-1', temperature=24)
        rh = self.fake('rh', UNASSIGNED, 'physical-1', humidity=53)
        result = approved_climate([t, rh], {'temp': 'mine'})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].room_id, 'mine')
        self.assertEqual(result[0].humidity, 53)
        self.assertEqual(result[0].temperature, 24)

    def test_different_relation_id_never_auto_added(self):
        from core import approved_climate, UNASSIGNED
        t = self.fake('temp', 'mine', 'physical-1', temperature=24)
        rh = self.fake('rh', UNASSIGNED, 'physical-2', humidity=55)
        self.assertIsNone(approved_climate([t, rh], {'temp': 'mine'})[0].humidity)

    def test_missing_relation_id_never_auto_added(self):
        from core import approved_climate, UNASSIGNED
        t = self.fake('temp', 'mine', None, temperature=24)
        rh = self.fake('rh', UNASSIGNED, None, humidity=55)
        self.assertIsNone(approved_climate([t, rh], {'temp': 'mine'})[0].humidity)

    def test_other_room_humidity_never_auto_added(self):
        from core import approved_climate
        t = self.fake('temp', 'mine', 'physical-1', temperature=24)
        rh = self.fake('rh', 'another-resident', 'physical-1', humidity=55)
        self.assertIsNone(approved_climate([t, rh], {'temp': 'mine'})[0].humidity)

    def test_two_approved_endpoint_records_merge_into_one(self):
        from core import approved_climate, UNASSIGNED
        t = self.fake('temp', 'mine', 'physical-1', temperature=24)
        rh = self.fake('rh', UNASSIGNED, 'physical-1', humidity=50)
        grouped = approved_climate([rh, t], {'temp': 'mine', 'rh': UNASSIGNED})
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].room_id, 'mine')
        self.assertEqual(grouped[0].humidity, 50)

    def test_no_sensors_approved_returns_nothing(self):
        from core import approved_climate, UNASSIGNED
        t = self.fake('temp', 'mine', 'pair', temperature=22)
        rh = self.fake('rh', UNASSIGNED, 'pair', humidity=61)
        self.assertEqual(approved_climate([t, rh], {}), [])

class TrayInputTests(unittest.TestCase):
    def setUp(self):
        from core import light_info
        self.mine = FakeLight('mine', 'myroom')
        self.mine2 = FakeLight('mine2', 'myroom')
        self.housemate = FakeLight('housemate', 'myroom')
        self.hub = Hub(self.mine, self.mine2, self.housemate)
        self.allow = {'mine': 'myroom', 'mine2': 'myroom'}

    def test_middle_click_only_toggles_approved(self):
        from core import toggle_power
        count, turn_on = toggle_power(self.hub, self.allow, ['mine', 'mine2'])
        self.assertEqual((count, turn_on), (2, True))
        self.assertEqual(self.mine.changed, [('power', True)])
        self.assertEqual(self.mine2.changed, [('power', True)])
        self.assertEqual(self.housemate.changed, [])

    def test_middle_click_any_on_means_all_off(self):
        from core import toggle_power
        self.mine2.attributes.is_on = True
        count, turn_on = toggle_power(self.hub, self.allow, ['mine', 'mine2'])
        self.assertEqual((count, turn_on), (2, False))
        self.assertEqual(self.mine.changed, [('power', False)])
        self.assertEqual(self.mine2.changed, [('power', False)])

    def test_middle_click_rejects_unapproved_before_writes(self):
        from core import toggle_power
        with self.assertRaises(PermissionError):
            toggle_power(self.hub, self.allow, ['mine', 'housemate'])
        self.assertEqual(self.mine.changed, [])
        self.assertEqual(self.housemate.changed, [])

    def test_middle_click_rejects_offline_before_writes(self):
        from core import toggle_power
        self.mine2.is_reachable = False
        with self.assertRaises(ValueError):
            toggle_power(self.hub, self.allow, ['mine', 'mine2'])
        self.assertEqual(self.mine.changed, [])
        self.assertEqual(self.mine2.changed, [])

    def test_middle_click_rejects_moved_before_writes(self):
        from core import toggle_power
        self.mine2.room.id = 'new-room'
        with self.assertRaises(PermissionError):
            toggle_power(self.hub, self.allow, ['mine', 'mine2'])
        self.assertEqual(self.mine.changed, [])

    def test_room_power_targets_doesnt_require_currently_on(self):
        from core import tray_power_targets, light_info
        lights = [light_info(v) for v in (self.mine, self.mine2, self.housemate)]
        self.assertEqual([x.id for x in tray_power_targets(lights, self.allow)], ['mine', 'mine2'])

    def test_standard_wheel_and_small_deltas(self):
        from core import wheel_steps
        self.assertEqual(wheel_steps(120), (1, 0))
        self.assertEqual(wheel_steps(-120), (-1, 0))
        self.assertEqual(wheel_steps(15), (0, 15))
        self.assertEqual(wheel_steps(15, 105), (1, 0))
        self.assertEqual(wheel_steps(-60, -60), (-1, 0))
        self.assertEqual(wheel_steps(-60, 60), (0, 0))
        self.assertEqual(wheel_steps(240, 15), (2, 15))

if __name__ == "__main__":
    unittest.main()
