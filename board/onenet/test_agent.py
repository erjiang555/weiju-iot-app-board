import importlib.util
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from onenet_agent import Bridge, Hardware, LocalControl


class CommandValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        for name, content in [('trigger', 'heartbeat'), ('brightness', '0'),
                              ('max_brightness', '255'), ('temp', '51039')]:
            with open(os.path.join(self.root, name), 'w') as stream:
                stream.write(content)
        self.hw = Hardware({'led_path': self.root,
                            'temperature_path': os.path.join(self.root, 'temp')},
                           os.path.join(self.root, 'state.json'))
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.hardware = self.hw
        self.bridge.prefix = 'test/'
        self.sent = []
        self.bridge.send = lambda suffix, data: self.sent.append((suffix, data))
        self.bridge.report = lambda: self.sent.append(('report', self.hw.values()))

    def tearDown(self):
        self.temp.cleanup()

    def request(self, params):
        self.bridge.on_message(None, None, SimpleNamespace(topic='test/set',
            payload=json.dumps({'id': '123', 'params': params}).encode()))

    def test_boolean_switch_and_readback(self):
        self.request({'led_switch': True})
        self.assertEqual(self.sent[0][1]['code'], 200)
        self.assertTrue(self.sent[1][1]['led_switch'])
        self.assertEqual(self.sent[1][1]['cpu_temperature'], 51.0)
        self.request({'led_switch': False})
        self.assertFalse(self.hw.values()['led_switch'])

    def test_reject_string_without_changing_led(self):
        self.request({'led_switch': 'false; reboot'})
        self.assertEqual(self.sent[0][1]['code'], 400)
        self.assertFalse(self.hw.values()['led_switch'])
        self.assertEqual(len(self.sent), 1)

    def test_readonly_property_rejects_whole_command(self):
        self.request({'led_switch': True, 'cpu_temperature': 20})
        self.assertEqual(self.sent[0][1]['code'], 400)
        self.assertFalse(self.hw.values()['led_switch'])

    def test_led_state_restores_after_restart(self):
        self.hw.set_led(True)
        with open(os.path.join(self.root, 'brightness'), 'w') as stream:
            stream.write('0')
        self.hw.initialize()
        self.assertTrue(self.hw.values()['led_switch'])

    def test_invalid_json_ignored(self):
        self.bridge.on_message(None, None, SimpleNamespace(topic='test/set', payload=b'bad json'))
        self.assertEqual(self.sent, [])
        self.assertFalse(self.hw.values()['led_switch'])

    def test_temperature_matches_half_degree_model(self):
        with open(os.path.join(self.root, 'temp'), 'w') as stream:
            stream.write('52900')
        self.assertEqual(self.hw.values()['cpu_temperature'], 53.0)

    def test_local_control_returns_no_credentials(self):
        control = LocalControl({'product_id': 'test-product', 'device_name': 'test-board',
                                'interval_seconds': 10}, self.hw,
                               os.path.join(self.root, 'control.sock'))
        try:
            result = control.handle({'action': 'set_led', 'value': True})
            self.assertTrue(result['ok'])
            self.assertTrue(result['status']['led_switch'])
            self.assertEqual(result['status']['last_control_source'], 'touchscreen')
            self.assertTrue(control.changed.is_set())
            self.assertNotIn('device_key', result['status'])
            self.assertNotIn('token', result['status'])
            with self.assertRaises(ValueError):
                control.handle({'action': 'set_led', 'value': 'false'})
        finally:
            control.server.close()


if __name__ == '__main__':
    unittest.main()
