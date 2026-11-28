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

    def test_flash_gate_and_cancel(self):
        a = self.app
        a.port = SimpleNamespace(is_open=True)
        a.update_controls()
        self.assertEqual(str(a.flash_button.cget('state')), 'disabled')
        with self.assertRaises(ValueError): a.save_flash()
        a.ram_verified = True
        a.update_controls()
        self.assertEqual(str(a.flash_button.cget('state')), 'normal')
        a.busy = True
        a.update_controls()
        self.assertEqual(str(a.flash_button.cget('state')), 'disabled')
        a.busy = False
        with patch('app.messagebox.askyesno', return_value=False) as dialog, \
             patch.object(a, 'run_job') as run:
            a.confirm_flash(copy.deepcopy(DEFAULT_SESSION))
            run.assert_not_called()
            self.assertIn('43000', dialog.call_args.args[1])
            self.assertEqual(dialog.call_args.kwargs['default'], 'no')

    def test_flash_rechecks_ram_and_waits_for_completion(self):
        a = self.app
        actual = copy.deepcopy(DEFAULT_SESSION)
        sent, waited, logged = [], [], []
        t = SimpleNamespace(wait_command=lambda *args: waited.append(args),
            log=SimpleNamespace(write=lambda *args, **kw: logged.append((args, kw))))
        a.exchange = lambda transport, request: sent.append(request)
        a.run_job = lambda job: job(t)
        with patch('app.messagebox.askyesno', return_value=True), \
             patch('app.read_device_session', return_value=copy.deepcopy(actual)):
            a.confirm_flash(actual)
        self.assertEqual([q[1:6].hex() for q in sent], ['0500000000', '050001ff00'])
        self.assertEqual(waited, [(a.settings['address'], 1)])
        self.assertEqual(logged[0][0], ('flash_saved',))
        changed = copy.deepcopy(actual)
        changed['channels'][0]['b'] = 5
        sent.clear(); waited.clear()
        with patch('app.messagebox.askyesno', return_value=True), \
             patch('app.read_device_session', return_value=changed), self.assertRaises(ValueError):
            a.confirm_flash(actual)
        self.assertEqual([q[1:6].hex() for q in sent], ['0500000000'])
        self.assertEqual(waited, [])

    def test_command_editor_builds_current_fields_and_is_readonly(self):
        from core import frame, ascii_encode
        a = self.app
        a.command_fields['address'].set('2')
        a.command_fields['function'].set('0x06 — Запись HR')
        a.command_fields['start'].set('0x001D')
        a.command_fields['data'].set('5')
        expected = frame(2, 6, 29, 5)
        self.assertEqual(a.build_request(), [expected])
        self.assertEqual(a.request_text.get('1.0', 'end').strip(), ascii_encode(expected).decode().strip())
        self.assertEqual(str(a.request_text.cget('state')), 'disabled')
        a.command_fields['data'].set('oops')
        with self.assertRaises(ValueError): a.build_request()
        self.assertEqual(a.request_text.get('1.0', 'end').strip(), '')
        a.port = SimpleNamespace(is_open=True)
        a.update_controls()
        self.assertEqual(str(a.command_widgets['count'].cget('state')), 'disabled')
        self.assertEqual(str(a.command_widgets['data'].cget('state')), 'normal')
        a.command_fields['function'].set('0x04 — Чтение IR')
        a.update_command_controls()
        self.assertEqual(str(a.command_widgets['data'].cget('state')), 'disabled')
        self.assertEqual(str(a.command_widgets['count'].cget('state')), 'normal')

    def test_calculation_updates_only_selected_channel_without_exchange(self):
        from calibration import ratio
        a = self.app
        original = [{key: var.get() for key, var in row.items()} for row in a.channels]
        i = 2
        a.channels[i]['rref_x10'].set('43000')
        a.calibration_inputs[i]['r0'].set('1000')
        for key, ref in [('zero', 0), ('fixed', 29.76)]:
            a.calibration_inputs[i]['adc_'+key].set(str(32768*1000/4300*ratio(ref/1.05)+20))
        with patch.object(a, 'run_job', side_effect=AssertionError('Must not send configuration')):
            a.calculate_channel(i)
        self.assertEqual(a.channels[i]['b'].get(), '-20')
        self.assertEqual(a.channels[i]['k_x100'].get(), '105')
        for channel in range(8):
            if channel != i:
                self.assertEqual({key: var.get() for key, var in a.channels[channel].items()}, original[channel])
        saved = a.session_value()
        a.calibration_inputs[i]['adc_zero'].set('')
        a.metadata.configure(state='normal')
        a.show_session(saved)
        self.assertEqual(a.calibration_inputs[i]['adc_zero'].get(), saved['calibration_points'][i]['adc_zero'])
        a.calibration_inputs[i]['adc_fixed'].set('')
        with self.assertRaises(ValueError):
            a.calculate_channel(i)
        self.assertEqual(a.channels[i]['b'].get(), '-20')

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
