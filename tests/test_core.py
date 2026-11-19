import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from core import *


class Port:
    def __init__(self, reply):
        self.reply = reply
    def reset_input_buffer(self):
        pass
    def write(self, data):
        return len(data)
    def read(self, count):
        value, self.reply = self.reply[:count], self.reply[count:]
        return value


def response(data):
    return data + struct.pack('<H', crc16(data))


class Tests(unittest.TestCase):
    def test_known_crc(self):
        self.assertEqual(frame(1, 3, 0, 10).hex(), '01030000000ac5cd')

    def test_configuration(self):
        s = copy.deepcopy(DEFAULT_SESSION)
        s['channels'][0]['b'] = -5
        frames = configuration_frames(s, 1)
        for f in frames:
            self.assertEqual(parse_frame(f.hex()), f)
        self.assertEqual(frames[1][25:27], b'\xff\xfb')
        self.assertEqual(frames[0], frame(1, 5, 0, 0))
        s['filters']['median_window'] = 4
        with self.assertRaises(ValueError):
            configuration_frames(s, 1)

    def test_invalid_frames(self):
        for f in [response(bytes.fromhex('010300000000')), response(bytes.fromhex('010500000001')), b'12345678']:
            with self.assertRaises(ValueError):
                parse_frame(f.hex())

    def test_transport_export_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
            regs = [65511, 0x8000]+[2500]*6+[0, 123, 0]+[0xffff]+[12345]*7
            reply = response(bytes([1, 4, 38])+struct.pack('>19H', *regs))
            values = Transport(Port(reply), .01, log).transact(frame(1, 4, 21, 19))
            self.assertEqual(values['T0_C'], -.25)
            self.assertIsNone(values['T1_C'])
            self.assertIsNone(values['ADC0'])
            self.assertEqual(values['rtc_utc_seconds'], 123)
            target = Path(directory)/'table.txt'
            self.assertEqual(export_log(log.path, target), 1)
            self.assertIn('NA', target.read_text(encoding='utf-8-sig'))
            header = json.loads(log.path.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(header['session'], DEFAULT_SESSION)

    def test_reject_bad_responses(self):
        request = frame(1, 3, 0, 1)
        cases = [b'', response(bytes.fromhex('018302')), response(bytes.fromhex('0203020001')),
                 response(bytes.fromhex('01030400010002')), bytes.fromhex('01030200010000')]
        with tempfile.TemporaryDirectory() as directory:
            for reply in cases:
                with self.subTest(reply=reply):
                    log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
                    with self.assertRaises((ValueError, TimeoutError, RuntimeError)):
                        Transport(Port(reply), .001, log).transact(request)
                    self.assertIn('"event": "error"', log.path.read_text())

    def test_wrong_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
            with self.assertRaises(ValueError):
                Transport(Port(frame(1, 6, 2, 4)), .01, log).transact(frame(1, 6, 2, 3))

    def test_json_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            s = copy.deepcopy(DEFAULT_SESSION)
            s['metadata']['Материал перемычки'] = 'медь'
            path = Path(directory)/'session.json'
            save_json(path, s)
            self.assertEqual(validate_session(read_json(path)), s)


if __name__ == '__main__':
    unittest.main()
