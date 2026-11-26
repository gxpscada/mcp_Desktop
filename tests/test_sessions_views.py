import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from core import DEFAULT_SESSION, DEFAULT_APP, ExchangeLog, frame, read_device_session, export_log
from presentation import validate_view, response_lines, series_values


class SessionViewTests(unittest.TestCase):
    def test_device_snapshot_signed_offset_mask_and_flags(self):
        words = [3]+[4300]*8+[65531]+[0]*7+[100]*8+[8, 5]
        calls = []
        def transact(q):
            calls.append(q)
            if q[1] == 1: return {'bits_hex': '02'}
            if int.from_bytes(q[2:4], 'big') == 29: return {'001D': 5}
            return {f'{i:04X}': v for i, v in enumerate(words)}
        s = read_device_session(SimpleNamespace(transact=transact), 1)
        self.assertEqual(s['channel_mask'], 5)
        self.assertEqual(s['channels'][0]['b'], -5)
        self.assertFalse(s['filters']['median_enabled'])
        self.assertTrue(s['filters']['average_enabled'])
        self.assertFalse(s['calibration_enabled'])
        self.assertEqual(s['filters']['average_window'], 8)
        self.assertEqual([q[1] for q in calls], [3, 3, 1])

    def test_view_switches_scaling_and_invalid_temperature(self):
        spec = {'view': {'lines': dict(tx_data=False, rx_data=False, modbus_field_type=False,
                                      json=False, human=True, status=False),
                         'values': [dict(title='valT0', value=dict(gain=.1, precision=2)),
                                    dict(title='valT1', value=dict(gain=.1, precision=2))]}}
        validate_view(spec)
        regs = {'0015': {'DEC': 65511}, '0016': {'DEC': 32768}}
        t = SimpleNamespace(last_ascii=':EXAMPLE\r\n', last_space='IR', last_registers=regs)
        q = frame(1, 4, 21, 2)
        self.assertEqual(series_values(q, regs, {}, spec), {'valT0': -2.5, 'valT1': None})
        lines = response_lines(q, t, {}, spec)
        self.assertEqual(len(lines), 1)
        self.assertIn('"valT0": "-2.50"', lines[0])
        spec['view']['lines']['human'] = False
        self.assertEqual(response_lines(q, t, {}, spec), [])

    def test_compact_log_verified_header_and_export_options(self):
        with tempfile.TemporaryDirectory() as folder:
            log = ExchangeLog(folder, DEFAULT_SESSION, DEFAULT_APP)
            s = copy.deepcopy(DEFAULT_SESSION)
            s.update(continuous_receive=True, request={'view': {'export_format':
                     {'first_line_titles': False, 'separator': ';'}}})
            log.set_session(s)
            log.write('rx', function=4, hex='ab '*300, registers={'0020': {'DEC': 123, 'HEX': '0x007B', 'BIN': '0000000001111011'}},
                      data={'ADC0': 123}, display_data={'sensor': 12.3})
            records = [json.loads(line) for line in log.path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[0]['configuration_status'], 'read_from_device')
            self.assertNotIn('hex', records[1])
            self.assertEqual(records[1]['registers'], {'0020': 123})
            self.assertTrue(json.loads((Path(folder)/'session.json').read_text())['continuous_receive'])
            output = Path(folder)/'out.txt'
            self.assertEqual(export_log(log.path, output), 1)
            self.assertTrue(output.read_text(encoding='utf-8-sig').strip().endswith(';12.3'))

    def test_chart_adc_flags_and_float_without_duplicate_words(self):
        q = frame(1, 4, 64, 2)
        values = series_values(q, {'0040': {'DEC': 1}, '0041': {'DEC': 2}},
                               {'0040': 1, '0041': 2, 'T0_C_float': 20.5}, {})
        self.assertEqual(values, {'T0_C_float': 20.5})
        self.assertEqual(series_values(frame(1, 1, 6, 1), {'0006': {'DEC': 1}}, {}, {}), {'0006': 1})
        self.assertEqual(series_values(frame(1, 4, 104, 1), {'0068': {'DEC': 65535}}, {}, {}), {'ADC0_Q1': None})
