import tempfile
import unittest
from core import *
from test_core import Port

class AsciiTests(unittest.TestCase):
    def test_known_wire(self):
        self.assertEqual(ascii_encode(frame(1,3,0,1)), b':010300000001FB\r\n')
        self.assertEqual(parse_frame(':010300000001FB'), frame(1,3,0,1))

    def test_bad_lrc_and_framing(self):
        for wire in (b':010300000001FA\r\n', b':010300000001FB\n', b':010300000001F\r\n', b':01030Z000001FB\r\n'):
            with self.assertRaises(ValueError): ascii_decode(wire)

    def test_transport_rejects_lrc(self):
        with tempfile.TemporaryDirectory() as d:
            log=ExchangeLog(d,DEFAULT_SESSION,DEFAULT_APP)
            p=Port(b'');p.reply=b':0103021234B5\r\n'
            with self.assertRaisesRegex(ValueError,'LRC'):
                Transport(p,.1,log).transact(frame(1,3,0,1))

    def test_maximum_response(self):
        body=bytes([1,3,250])+bytes(250)
        packet=body+struct.pack('<H',crc16(body))
        wire=ascii_encode(packet)
        self.assertEqual(len(wire),511)
        self.assertEqual(ascii_decode(wire),packet)
