import unittest
from commands import build_command, describe_command, integer
from core import frame, ascii_encode, Transport
from types import SimpleNamespace


class CommandTests(unittest.TestCase):
    def test_binary_fields_and_hex_ending_b(self):
        for text in ('01111011b', '01111011B', '0b01111011', '0B01111011', '0x7b'):
            self.assertEqual(integer(text, 'Данные'), 123)
        self.assertEqual(build_command('1b', 4, '10101b', '1000b', ''), frame(1, 4, 21, 8))
        self.assertEqual(build_command('1', 16, '0', '', '01111011b 0x7b 123'), frame(1, 16, 0, [123]*3))
        for text in ('102b', 'b', '0b', '0b102', '1_1b', '10000000000000000b'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                integer(text, 'Данные')

    def test_read_and_write_fields(self):
        self.assertEqual(build_command('1', 4, '0x0015', '8', ''), frame(1, 4, 21, 8))
        self.assertEqual(build_command('1', 5, '0', '', '0xFF00'), frame(1, 5, 0, 65280))
        self.assertEqual(build_command('1', 16, '0x10', '', '100, 0xFFFF; 2'), frame(1, 16, 16, [100, 65535, 2]))

    def test_reject_invalid_fields(self):
        for args in [('0', 4, '0', '1', ''), ('1', 4, '0', '0', ''),
                     ('1', 4, '0xFFFF', '2', ''), ('1', 5, '0', '', '1'),
                     ('1', 16, '0', '', ''), ('1', 6, '0', '', '-1'),
                     ('1', 16, '65535', '', '1 2')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_command(*args)

    def test_description_and_notification_after_actual_write(self):
        request = frame(1, 5, 0, 65280)
        text = describe_command(request)
        self.assertEqual(text, '//Записать start')
        self.assertEqual(describe_command(frame(1, 4, 21, 8)), '//Читать температуры')
        self.assertEqual(describe_command(request, 'Записать START'), '//Записать START')
        self.assertEqual(describe_command(request, 'включить МЕДИАННЫЙ фильтр'), '//Включить МЕДИАННЫЙ фильтр')
        events = []
        wire = bytearray(ascii_encode(request))
        def read(n):
            data = bytes(wire[:n]); del wire[:n]
            return data
        port = SimpleNamespace(reset_input_buffer=lambda: None, read=read,
                               write=lambda data: events.append('write') or len(data))
        t = Transport(port, .1, SimpleNamespace(write=lambda *args, **kw: None))
        t.on_sent = lambda q: events.append(describe_command(q))
        t.transact(request)
        self.assertEqual(events, ['write', text])
        events.clear()
        port.write = lambda data: 0
        with self.assertRaises(IOError): t.transact(request)
        self.assertEqual(events, [])
