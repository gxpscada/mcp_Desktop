import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from core import *


class Port:
    def __init__(self, reply):
        self.reply = ascii_encode(reply) if reply else reply
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
            self.assertEqual(parse_frame(ascii_encode(f).decode()), f)
        self.assertEqual(frames[1][25:27], b'\xff\xfb')
        self.assertEqual(frames[0], frame(1, 5, 0, 0))
        s['filters']['median_window'] = 4
        with self.assertRaises(ValueError):
            configuration_frames(s, 1)

    def test_sparse_channels_and_legacy_session(self):
        s = copy.deepcopy(DEFAULT_SESSION)
        s.update(channel_count=3, channel_mask=5)
        frames = configuration_frames(s, 1)
        self.assertEqual(frames[2], frame(1, 6, 0x1d, 5))
        del s['channel_mask']
        self.assertEqual(configuration_frames(s, 1)[2], frame(1, 6, 0x1d, 7))
        for mask in (0, 256, 3, True):
            s['channel_mask'] = mask
            with self.assertRaises(ValueError):
                validate_session(s)

    def test_register_log_formats(self):
        q = frame(1, 4, 9, 1)
        r = response(bytes.fromhex('0104024000'))
        self.assertEqual(register_view(q, r), ('IR', {
            '0009': {'HEX': '0x4000', 'BIN': '0100000000000000', 'DEC': 16384}}))
        q = frame(1, 2, 7, 3)
        self.assertEqual(register_view(q, response(bytes.fromhex('01020105'))), ('DI', {
            '0007': {'HEX': '0x0001', 'BIN': '1', 'DEC': 1},
            '0008': {'HEX': '0x0000', 'BIN': '0', 'DEC': 0},
            '0009': {'HEX': '0x0001', 'BIN': '1', 'DEC': 1}}))

    def test_float_temperature_and_invalid(self):
        words = struct.unpack('<HH', struct.pack('<f', 850.0))
        r = response(bytes([1, 4, 8]) + struct.pack('>4H', *words, 0, 0x7fc0))
        data = decode(frame(1, 4, 0x40, 4), r)
        self.assertEqual(data['T0_C_float'], 850.0)
        self.assertIsNone(data['T1_C_float'])

    def test_stream_receive_without_retransmit(self):
        class StreamPort(Port):
            def reset_input_buffer(self):
                raise AssertionError('Must not discard streamed data')
            def write(self, data):
                raise AssertionError('Must not repeat the request')
        with tempfile.TemporaryDirectory() as directory:
            log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
            t = Transport(StreamPort(response(bytes.fromhex('010402007B'))), .01, log)
            self.assertEqual(t.transact(frame(1, 4, 9, 1), send_request=False)['0009'], 123)

    def test_stream_stop_skips_inflight_read(self):
        with tempfile.TemporaryDirectory() as directory:
            log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
            stop = frame(1, 5, 9, 0)
            p = Port(b'')
            p.reply = ascii_encode(response(bytes.fromhex('010402007B'))) + ascii_encode(stop)
            self.assertEqual(Transport(p, .01, log).transact(stop, ignore_functions=(4,)), {'acknowledged': True})

    def test_stream_cancel_while_idle(self):
        import threading
        stop = threading.Event(); stop.set()
        with tempfile.TemporaryDirectory() as directory:
            log = ExchangeLog(directory, DEFAULT_SESSION, DEFAULT_APP)
            t = Transport(Port(b''), .01, log)
            self.assertIsNone(t.transact(frame(1, 4, 9, 1), send_request=False, stop=stop))

    def test_invalid_frames(self):
        for f in [response(bytes.fromhex('010300000000')), response(bytes.fromhex('010500000001')), b'12345678']:
            with self.assertRaises(ValueError):
                parse_frame(ascii_encode(f).decode())

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
                 response(bytes.fromhex('01030400010002')), response(bytes.fromhex('0104020001'))]
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
