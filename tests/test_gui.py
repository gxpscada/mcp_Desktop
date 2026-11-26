import unittest
import copy
import json
import csv
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from app import App
from core import configuration_frames, ExchangeLog, DEFAULT_SESSION, DEFAULT_APP

class GuiTests(unittest.TestCase):
    def setUp(self):
        self.app=App(); self.app.withdraw()
    def tearDown(self):
        self.app.destroy()
    def test_plot_missing_and_bounded(self):
        p=self.app.plot
        p.add(10, {'T0_C':20, 'T1_C':None})
        p.add(11, {'T0_C':None, 'T1_C':22})
        p.add(12, {'T0_C_float':20.125, 'T0_C':20.12})
        self.assertEqual(p.points[-1][1][0],20.125)
        self.assertIsNone(p.points[1][1][0])
        for i in range(2200): p.add(13+i,{'T0_C':20+i%3})
        self.assertEqual(len(p.points),2000)
        p.redraw()
        self.assertTrue(p.canvas.find_all())
    def test_configuration_waits_for_stream_stop(self):
        a=self.app
        a.port=SimpleNamespace(is_open=True)
        a.streaming=True; a.busy=True
        s=a.session_value(); frames=configuration_frames(s,a.settings['address'])
        sent=[]
        a.exchange=lambda t,request: sent.append(request)
        a.configure_device()
        self.assertTrue(a.stream_stop.is_set())
        self.assertFalse(a.streaming)
        self.assertEqual(sent,[])
        self.assertTrue(a.busy)
        expected=frames[1][7:-2]
        class Transport:
            log=SimpleNamespace(write=lambda *args,**kwargs:None)
            def transact(self,request):
                if request[1]==1: return {'bits_hex':'07'}
                if int.from_bytes(request[2:4],'big')==29: return {'001D':s['channel_mask']}
                return {str(i):int.from_bytes(expected[i:i+2],'big') for i in range(0,len(expected),2)}
        a.after_stream_job(Transport())
        self.assertEqual(sent,frames)
        self.assertEqual(sent[0][1:6],bytes.fromhex('0500000000'))

    def test_numeric_plot_and_missing_samples(self):
        p = self.app.plot
        p.add(1, {'ADC0': 123, 'ADC1': None})
        p.add(2, {'ADC0': 124, 'ADC1': 200})
        self.assertEqual(p.names, ['ADC0', 'ADC1'])
        self.assertIsNone(p.points[0][1][1])
        p.group.set('АЦП')
        p.redraw()
        self.assertTrue(p.canvas.find_all())

    def test_csv_full_history_selection_missing_and_clear(self):
        p = self.app.plot
        p.add(10, {'ADC0': 123, 'T0_C': 20})
        p.add(10.25, {'ADC0': None, 'ADC1': 456})
        for i in range(2100):
            p.add(11+i, {'ADC0': i})
        p.group.set('АЦП')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'plot.csv'
            self.assertEqual(p.export_csv(path), 2102)
            with path.open(encoding='utf-8-sig', newline='') as source:
                rows = list(csv.reader(source))
            self.assertEqual(rows[0], ['time_s', 'ADC0', 'ADC1'])
            self.assertEqual(rows[1], ['0.000000000', '123', ''])
            self.assertEqual(rows[2], ['0.250000000', '', '456'])
            self.assertEqual(len(p.points), 2000)
            p.add(2112, {'ADC0': 99})
            self.assertEqual(p.export_csv(path), 2103)
            p.clear()
            p.add(50, {'ADC0': 8})
            self.assertEqual(p.export_csv(path), 1)
            with path.open(encoding='utf-8-sig', newline='') as source:
                self.assertEqual(list(csv.reader(source)), [['time_s', 'ADC0'], ['0.000000000', '8']])

    def test_connection_saves_device_configuration(self):
        a = self.app
        actual = copy.deepcopy(DEFAULT_SESSION)
        actual.update(channel_count=1, channel_mask=1, configuration_status='read_from_device')
        actual['channels'][0]['rref_x10'] = 4300
        port = SimpleNamespace(is_open=True, close=lambda: None)
        a.run_job = lambda job: job(SimpleNamespace())
        with tempfile.TemporaryDirectory() as folder, patch('app.ROOT', Path(folder)), \
             patch('serial.Serial', return_value=port), \
             patch('app.read_device_session', return_value=actual):
            a.connect()
            saved = json.loads((Path(folder)/'session.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['channels'][0]['rref_x10'], 4300)
            self.assertFalse(saved['continuous_receive'])
            header = json.loads(a.log.path.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(header['session'], saved)
            self.assertEqual(header['configuration_status'], 'read_from_device')

    def test_each_stream_has_separate_verified_session(self):
        a = self.app
        a.port = SimpleNamespace(is_open=True)
        a.update_controls()
        a.request_choice.current(2)  # ADC request from Maxim's file
        a.preview_request()
        with tempfile.TemporaryDirectory() as folder, patch('app.ROOT', Path(folder)):
            previous = ExchangeLog(Path(folder)/'connection', DEFAULT_SESSION, DEFAULT_APP)
            t = SimpleNamespace(log=previous, last_registers={f'{32+i:04X}': {'DEC': 123} for i in range(8)},
                                transact=lambda *args, **kwargs: {'acknowledged': True})
            def exchange(transport, request):
                if request[1] == 4:
                    a.stream_stop.set()
                    return {f'ADC{i}': 123 for i in range(8)}
                return {'acknowledged': True}
            a.exchange = exchange
            a.run_job = lambda job: job(t)
            with patch('app.read_device_session', side_effect=lambda *args: copy.deepcopy(DEFAULT_SESSION)):
                a.start_stream()
                a.start_stream()
            sessions = list(Path(folder).glob('logs/*/session.json'))
            self.assertEqual(len(sessions), 2)
            for path in sessions:
                s = json.loads(path.read_text(encoding='utf-8'))
                self.assertTrue(s['continuous_receive'])
                self.assertEqual(s['request']['start'], 32)
            self.assertIs(t.log, previous)
