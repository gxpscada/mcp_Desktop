"""Russian desktop GUI for stage one. Run: python app.py."""
import copy
import json
import queue
import threading
import time
from datetime import datetime, timezone
from presentation import validate_view, line_enabled, series_values, response_lines
from plot import TemperaturePlot
from calibration import calculate as calculate_calibration
from commands import FUNCTIONS, build_command, describe_command
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
from core import (DEFAULT_APP, DEFAULT_SESSION, DEFAULT_REQUESTS, read_json, save_json,
                  validate_session, configuration_frames, request_frames, parse_frame,
                  ExchangeLog, Transport, export_log, frame, ascii_encode, read_device_session)

ROOT = Path(__file__).resolve().parent


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Температуры • PT100 | mcp_Desktop • блокировка COM')
        self.geometry('1140x850')
        self.minsize(960, 740)
        self.port = None
        self.log = None
        self.active_spec = {}
        self.ram_verified = False
        self.pending_flash_preview = None
        self.busy = False
        self.streaming = False
        self.stream_stop = threading.Event()
        self.after_stream_job = None
        self.pending_requests = []
        self.device_buttons = []
        self.events = queue.Queue()
        self.session = copy.deepcopy(DEFAULT_SESSION)
        self.session_path = ROOT/'sessions/session01.json'
        self.settings = read_json(ROOT/'application.json') if (ROOT/'application.json').exists() else DEFAULT_APP.copy()
        self.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('TButton', padding=(10, 6))
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Температуры', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='PT100 / MAX31865   •   Настройка и одиночный обмен   •   Modbus ASCII').pack(anchor='w', pady=(2, 14))
        connection = ttk.LabelFrame(outer, text='Подключение', padding=10)
        connection.pack(fill='x')
        self.connection_vars = {}
        for i, (key, label, width, values) in enumerate([
            ('port', 'COM-порт', 12, []), ('baudrate', 'Бод', 10, [9600, 115200, 921600]),
            ('parity', 'Чётность', 5, ['N', 'E', 'O']), ('stopbits', 'Стоп-биты', 5, [1, 2]),
            ('address', 'Адрес', 5, []), ('timeout', 'Таймаут, с', 8, [])]):
            ttk.Label(connection, text=label).grid(row=0, column=i, sticky='w', padx=4)
            var = tk.StringVar(value=str(self.settings[key]))
            self.connection_vars[key] = var
            entry = ttk.Combobox(connection, textvariable=var, width=width, values=values)
            entry.grid(row=1, column=i, padx=4)
            if key == 'port':
                self.port_choice = entry
        ttk.Button(connection, text='Обновить порты', command=self.refresh_ports).grid(row=1, column=6, padx=5)
        self.connect_button = ttk.Button(connection, text='Подключить', command=self.connect)
        self.connect_button.grid(row=1, column=7)
        ttk.Button(connection, text='Сохранить', command=lambda: self.guard(self.save_settings)).grid(row=1, column=8, padx=5)
        self.status = tk.StringVar(value='Не подключено • параметры линии: 8 бит данных')
        status_bar = ttk.Frame(outer)
        status_bar.pack(fill='x', pady=8)
        ttk.Button(status_bar, text='Экспорт .log → .txt', command=lambda: self.guard(self.export)).pack(side='right', padx=(8, 0))
        ttk.Label(status_bar, textvariable=self.status, wraplength=700).pack(side='left', fill='x', expand=True)
        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        config_page = ttk.Frame(tabs)
        config_canvas = tk.Canvas(config_page, highlightthickness=0)
        config_scroll = ttk.Scrollbar(config_page, orient='vertical', command=config_canvas.yview)
        config_horizontal = ttk.Scrollbar(config_page, orient='horizontal', command=config_canvas.xview)
        config_horizontal.pack(side='bottom', fill='x')
        config_scroll.pack(side='right', fill='y')
        config_canvas.pack(side='left', fill='both', expand=True)
        config_canvas.configure(yscrollcommand=config_scroll.set, xscrollcommand=config_horizontal.set)
        config = ttk.Frame(config_canvas, padding=12)
        config_window = config_canvas.create_window((0, 0), window=config, anchor='nw')
        config.bind('<Configure>', lambda _: config_canvas.configure(scrollregion=config_canvas.bbox('all')))
        config_canvas.bind('<Configure>', lambda event: config_canvas.itemconfigure(config_window, width=max(event.width, config.winfo_reqwidth())))
        requests = ttk.Frame(tabs, padding=12)
        tabs.add(config_page, text='  Сеанс и калибровка  ')
        tabs.add(requests, text='  Запросы и ответы  ')
        self.plot = TemperaturePlot(tabs)
        tabs.add(self.plot, text='  Графики данных  ')
        bar = ttk.Frame(config)
        bar.pack(fill='x')
        for text, action in [('Открыть JSON', self.load_session), ('Сохранить как…', self.save_session),
                             ('Добавить поле', self.add_field), ('Отправить конфигурацию', self.configure_device)]:
            button = ttk.Button(bar, text=text, command=lambda fn=action: self.guard(fn))
            button.pack(side='left', padx=(0, 6))
            if action == self.configure_device:
                self.device_buttons.append(button)
        self.flash_button = ttk.Button(bar, text='Сохранить во Flash', command=lambda: self.guard(self.save_flash))
        self.flash_button.pack(side='left', padx=(0, 6))
        self.session_label = tk.StringVar(value=str(self.session_path.name))
        ttk.Label(config, textvariable=self.session_label).pack(anchor='w', pady=8)
        options = ttk.Frame(config)
        options.pack(fill='x')
        self.channel_enabled = [tk.BooleanVar(value=i < self.session['channel_count']) for i in range(8)]
        self.median = tk.BooleanVar(value=True)
        self.average = tk.BooleanVar(value=True)
        self.calibration = tk.BooleanVar(value=True)
        self.median_window = tk.StringVar(value='3')
        self.average_window = tk.StringVar(value='4')
        for label, enabled, window, values in [('Медиана', self.median, self.median_window, [3, 5, 7]), ('Среднее', self.average, self.average_window, [2, 4, 8, 16])]:
            ttk.Checkbutton(options, text=label, variable=enabled).pack(side='left', padx=5)
            ttk.Combobox(options, textvariable=window, values=values, state='readonly', width=4).pack(side='left')
        ttk.Checkbutton(options, text='Калибровка', variable=self.calibration).pack(side='left', padx=12)
        table = ttk.Frame(config)
        table.pack(fill='x', pady=10)
        for col, label in enumerate(['Канал', 'Rref ×10, Ом', 'b, отсчёты АЦП', 'k ×100 (100 = 1,00)']):
            ttk.Label(table, text=label).grid(row=0, column=col, sticky='w', padx=10, pady=3)
        self.channels = []
        for i in range(8):
            ttk.Checkbutton(table, text=f'T{i}', variable=self.channel_enabled[i]).grid(row=i+1, column=0, padx=10)
            row = {}
            for col, key in enumerate(('rref_x10', 'b', 'k_x100'), 1):
                row[key] = tk.StringVar(value=str(self.session['channels'][i][key]))
                ttk.Entry(table, textvariable=row[key], width=20).grid(row=i+1, column=col, padx=10, pady=2)
            self.channels.append(row)
        ttk.Label(config, text='Фильтры общие. b применяется к АЦП до CVD; k — к температуре. Число каналов ограничено сборкой платы.').pack(anchor='w')
        calibration_box = ttk.LabelFrame(config, text='Расчёт калибровки по эталонным точкам', padding=8)
        calibration_box.pack(fill='x', pady=10)
        ttk.Label(calibration_box, text='Температуры вводите при b = 0, k = 1 (k ×100 = 100). АЦП — до коррекции, 15 бит; Q1 делите на 2.').pack(anchor='w')
        ttk.Label(calibration_box, text='Нужны две разные точки. При заполнении АЦП и температуры одной точки используется АЦП. R0 должен совпадать с прошивкой.').pack(anchor='w')
        calibration_table = ttk.Frame(calibration_box)
        calibration_table.pack(fill='x', pady=6)
        fields = [('r0', 'Тип датчика / R0, Ом', '1000'),
                  ('reference', 'Произвольный эталон, °C', ''),
                  ('adc_zero', 'АЦП при эталоне 0 °C', ''),
                  ('adc_fixed', 'АЦП при эталоне 29,76 °C', ''),
                  ('adc_custom', 'АЦП при произвольном эталоне', ''),
                  ('temp_zero', 'Температура при эталоне 0 °C', ''),
                  ('temp_fixed', 'Температура при эталоне 29,76 °C', ''),
                  ('temp_custom', 'Температура при произвольном эталоне', '')]
        for row_index, (_, label, _) in enumerate(fields, 1):
            ttk.Label(calibration_table, text=label).grid(row=row_index, column=0, sticky='w', pady=2)
        self.calibration_inputs = []
        for i in range(8):
            ttk.Label(calibration_table, text=f'T{i}').grid(row=0, column=i+1)
            variables = {}
            for row_index, (key, _, default) in enumerate(fields, 1):
                variable = tk.StringVar(value=default)
                variables[key] = variable
                if key == 'r0':
                    widget = ttk.Combobox(calibration_table, textvariable=variable, values=('100', '1000'), state='readonly', width=7)
                else:
                    widget = ttk.Entry(calibration_table, textvariable=variable, width=9)
                widget.grid(row=row_index, column=i+1, padx=2, pady=2, sticky='ew')
            self.calibration_inputs.append(variables)
            ttk.Button(calibration_table, text='Расчёт', command=lambda channel=i: self.guard(
                lambda: self.calculate_channel(channel))).grid(row=len(fields)+1, column=i+1, padx=2, pady=5)
        self.calculation_status = tk.StringVar(value='Результат подставляется в b и k ×100 выбранного датчика. Отправка — кнопкой «Отправить конфигурацию».')
        ttk.Label(calibration_box, textvariable=self.calculation_status, wraplength=900).pack(anchor='w')
        ttk.Label(config, text='Дополнительные поля сеанса (JSON; сохраняются на ПК)').pack(anchor='w', pady=(12, 4))
        self.metadata = tk.Text(config, height=4, font=('Consolas', 10), wrap='word')
        self.metadata.pack(fill='both', expand=True)
        self.metadata.insert('1.0', '{}')
        ttk.Label(config, text='Отправка: STOP → настройки → флаги → проверка чтением. Измерение остаётся остановленным.').pack(anchor='w')
        request_bar = ttk.Frame(requests)
        request_bar.pack(fill='x')
        self.request_choice = ttk.Combobox(request_bar, state='readonly', width=43)
        self.request_choice.pack(side='left', padx=(0, 8))
        self.request_choice.bind('<<ComboboxSelected>>', lambda e: self.guard(self.preview_request))
        ttk.Button(request_bar, text='Reload requests.json', command=lambda: self.guard(self.reload_requests)).pack(side='left')
        ttk.Button(request_bar, text='Сформировать', command=lambda: self.guard(self.build_request)).pack(side='left', padx=8)
        editor = ttk.LabelFrame(requests, text='Параметры команды', padding=6)
        editor.pack(fill='x', pady=6)
        self.command_fields = {key: tk.StringVar() for key in ('address', 'function', 'start', 'count', 'data')}
        self.command_widgets = {}
        for column, (key, title, width) in enumerate([
            ('address', 'Адрес устройства', 8), ('function', 'Функция Modbus', 27),
            ('start', 'Начальный регистр / COIL', 12), ('count', 'Количество для чтения', 9)]):
            ttk.Label(editor, text=title).grid(row=0, column=column, sticky='w', padx=4)
            if key == 'function':
                widget = ttk.Combobox(editor, textvariable=self.command_fields[key], state='readonly', width=width,
                    values=[f'0x{fc:02X} — {name}' for fc, name in FUNCTIONS.items()])
                widget.bind('<<ComboboxSelected>>', lambda _: self.update_command_controls())
            else:
                widget = ttk.Entry(editor, textvariable=self.command_fields[key], width=width)
            widget.grid(row=1, column=column, sticky='ew', padx=4)
            self.command_widgets[key] = widget
        ttk.Label(editor, text='Данные для записи').grid(row=2, column=0, sticky='w', padx=4)
        self.command_widgets['data'] = ttk.Entry(editor, textvariable=self.command_fields['data'])
        self.command_widgets['data'].grid(row=2, column=1, columnspan=3, sticky='ew', padx=4, pady=5)
        self.command_hint = tk.StringVar()
        ttk.Label(editor, textvariable=self.command_hint, wraplength=950).grid(row=3, column=0, columnspan=4, sticky='w', padx=4)
        self.editor_rtc = False
        ttk.Label(requests, text='Полученная команда: Modbus ASCII + LRC (CRLF добавляется при отправке)').pack(anchor='w')
        self.request_text = tk.Text(requests, height=3, font=('Consolas', 12), wrap='word', state='disabled')
        self.request_text.pack(fill='x')
        send_button = ttk.Button(requests, text='Отправить кадры', command=lambda: self.guard(self.send_requests))
        send_button.pack(anchor='w', pady=10)
        self.device_buttons.append(send_button)
        self.start_stream_button = ttk.Button(requests, text='Непрерывное чтение', command=lambda: self.guard(self.start_stream))
        self.start_stream_button.pack(anchor='w')
        self.stop_stream_button = ttk.Button(requests, text='Остановить поток', command=self.stream_stop.set)
        self.stop_stream_button.pack(anchor='w')
        output_bar = ttk.Frame(requests)
        output_bar.pack(fill='x')
        ttk.Label(output_bar, text='Ответы и события').pack(side='left')
        self.clear_output_button = ttk.Button(output_bar, text='Очистить', command=self.clear_output)
        self.clear_output_button.pack(side='right')
        self.output = tk.Text(requests, height=12, font=('Consolas', 10), state='disabled', wrap='word')
        self.output.pack(fill='both', expand=True)
        self.reload_requests()
        if self.session_path.exists():
            self.show_session(validate_session(read_json(self.session_path)))
        self.device_controls = []
        def collect(parent):
            for widget in parent.winfo_children():
                if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry, ttk.Combobox, ttk.Spinbox, tk.Text)) and widget not in (self.output, self.clear_output_button):
                    self.device_controls.append((widget, str(widget.cget('state'))))
                collect(widget)
        collect(config)
        config_count = len(self.device_controls)
        collect(requests)
        self.request_controls = self.device_controls[config_count:]
        self.update_controls()
        self.after(80, self.poll_events)

    def update_controls(self):
        connected = self.port is not None and self.port.is_open
        for widget, initial_state in self.device_controls:
            widget.configure(state=initial_state if connected and not self.busy else "disabled")
        self.connect_button.state(["disabled" if self.busy else "!disabled"])
        if self.streaming:
            for widget, state in self.device_controls:
                if widget is not self.start_stream_button:
                    widget.configure(state=state)
        self.stop_stream_button.configure(state="normal" if self.streaming else "disabled")
        self.flash_button.configure(state='normal' if connected and self.ram_verified and not self.busy else 'disabled')
        self.update_command_controls()

    def guard(self, fn):
        try:
            fn()
        except Exception as exc:
            messagebox.showerror('Ошибка', str(exc), parent=self)

    def settings_value(self):
        s = {key: var.get().strip() for key, var in self.connection_vars.items()}
        for key in ('baudrate', 'address'):
            s[key] = int(s[key])
        s['stopbits'], s['timeout'] = float(s['stopbits']), float(s['timeout'])
        if not s['port'] or not 1 <= s['address'] <= 247 or not .1 <= s['timeout'] <= 60 or not 1 <= s['baudrate'] <= 4000000 or s['parity'] not in ('N', 'E', 'O') or s['stopbits'] not in (1, 2):
            raise ValueError('Проверьте параметры подключения')
        return dict(s, bytesize=8, schema_version=1, protocol="modbus-ascii")

    def save_settings(self):
        save_json(ROOT/'application.json', self.settings_value())
        self.status.set('Настройки подключения сохранены')

    def refresh_ports(self):
        def action():
            from serial.tools import list_ports
            self.port_choice['values'] = [p.device for p in list_ports.comports()]
        self.guard(action)

    def session_value(self):
        s = copy.deepcopy(self.session)
        mask = sum(1 << i for i, enabled in enumerate(self.channel_enabled) if enabled.get())
        s.update(channel_count=mask.bit_length(), channel_mask=mask, calibration_enabled=self.calibration.get(),
                 channels=[{key: int(var.get()) for key, var in c.items()} for c in self.channels],
                 metadata=json.loads(self.metadata.get('1.0', 'end')))
        s['filters'].update(median_enabled=self.median.get(), average_enabled=self.average.get(),
                            median_window=int(self.median_window.get()), average_window=int(self.average_window.get()))
        s['calibration_points'] = [{key: var.get() for key, var in row.items()} for row in self.calibration_inputs]
        return validate_session(s)

    def calculate_channel(self, channel):
        inputs = {key: var.get() for key, var in self.calibration_inputs[channel].items()}
        result = calculate_calibration(inputs, self.channels[channel]['rref_x10'].get())
        self.channels[channel]['b'].set(str(result['b']))
        self.channels[channel]['k_x100'].set(str(result['k_x100']))
        self.calibration.set(True)
        self.calculation_status.set(f"T{channel}: b = {result['b']}, k ×100 = {result['k_x100']}; "
            f"точек: {result['points']}, максимальная расчётная ошибка после округления: {result['max_error']:.3f} °C. "
            'Значения подставлены; в прибор ещё не отправлены.')

    def show_session(self, s):
        self.session = copy.deepcopy(s)
        for i, row in enumerate(s.get('calibration_points', [])[:8]):
            for key, value in row.items():
                if key in self.calibration_inputs[i]:
                    self.calibration_inputs[i][key].set(str(value))
        mask = s.get('channel_mask', (1 << s['channel_count']) - 1)
        for i, enabled in enumerate(self.channel_enabled):
            enabled.set(bool(mask & (1 << i)))
        self.calibration.set(s['calibration_enabled'])
        for var, key in [(self.median, 'median_enabled'), (self.average, 'average_enabled'), (self.median_window, 'median_window'), (self.average_window, 'average_window')]:
            var.set(s['filters'][key])
        for row, c in zip(self.channels, s['channels']):
            for key, var in row.items():
                var.set(str(c[key]))
        self.metadata.delete('1.0', 'end')
        self.metadata.insert('1.0', json.dumps(s['metadata'], ensure_ascii=False, indent=2))
        self.session_label.set(str(self.session_path))

    def load_session(self):
        path = filedialog.askopenfilename(initialdir=ROOT/'sessions', filetypes=[('JSON', '*.json')])
        if path:
            s = validate_session(read_json(path))
            self.session_path = Path(path)
            self.show_session(s)

    def save_session(self):
        s = self.session_value()
        path = filedialog.asksaveasfilename(initialdir=ROOT/'sessions', initialfile=self.session_path.name, defaultextension='.json', filetypes=[('JSON', '*.json')])
        if path:
            save_json(path, s)
            self.session_path = Path(path)
            self.show_session(s)

    def add_field(self):
        data = json.loads(self.metadata.get('1.0', 'end'))
        name = simpledialog.askstring('Дополнительное поле', 'Название поля:', parent=self)
        if not name:
            return
        if name in data:
            raise ValueError('Поле уже существует; измените его в редакторе JSON')
        value = simpledialog.askstring('Значение', 'Значение (текст или JSON):', parent=self)
        if value is None:
            return
        try:
            data[name] = json.loads(value)
        except json.JSONDecodeError:
            data[name] = value
        self.metadata.delete('1.0', 'end')
        self.metadata.insert('1.0', json.dumps(data, ensure_ascii=False, indent=2))

    def reload_requests(self):
        path = ROOT/'requests.json'
        values = read_json(path) if path.exists() else DEFAULT_REQUESTS
        if not isinstance(values, list) or not values:
            raise ValueError('requests.json должен содержать непустой массив')
        for request in values:
            validate_view(request)
            if not isinstance(request['title'], str):
                raise ValueError('title должен быть строкой')
            for data in request_frames(request, 1):
                parse_frame(ascii_encode(data).decode())
        self.requests = values
        self.request_choice['values'] = [r['title'] for r in values]
        self.request_choice.current(0)
        self.preview_request()

    def preview_request(self):
        address = self.settings['address'] if self.port else self.settings_value()['address']
        selected = self.requests[self.request_choice.current()]
        self.editor_rtc = selected.get('action') == 'rtc'
        self.command_fields['address'].set(str(address))
        fc = selected.get('function', 16)
        self.command_fields['function'].set(f'0x{fc:02X} — {FUNCTIONS[fc]}')
        self.command_fields['start'].set(f"0x{selected.get('start', 27):04X}")
        self.command_fields['count'].set(str(selected.get('count', 1)))
        data = selected.get('values', [selected.get('value', 0)])
        self.command_fields['data'].set(' '.join(f'0x{v:04X}' for v in data))
        self.update_command_controls()
        self.build_request()

    def update_command_controls(self):
        if not hasattr(self, 'command_widgets'):
            return
        connected = self.port is not None and self.port.is_open
        enabled = connected and (not self.busy or self.streaming)
        fc = int(self.command_fields['function'].get().split(' ')[0], 16) if self.command_fields['function'].get() else 4
        for key, widget in self.command_widgets.items():
            available = enabled and (not self.editor_rtc or key == 'address')
            if key == 'count': available = available and fc in (1, 2, 3, 4)
            if key == 'data': available = available and fc in (5, 6, 16)
            widget.configure(state=('readonly' if key == 'function' else 'normal') if available else 'disabled')
        if self.editor_rtc:
            hint = 'RTC: последовательность STOP → запись времени ПК → установка RTC. Время обновляется при отправке.'
        elif fc == 5:
            hint = 'DEC, HEX (0x7B), BIN (01111011b / 0b01111011). COIL: 1111111100000000b — включить, 0 — выключить.'
        elif fc == 16:
            hint = 'DEC, HEX (0x7B), BIN (01111011b / 0b01111011). Значения разделяйте пробелом, запятой или ;. Количество определяется автоматически.'
        else:
            hint = 'Числа: DEC (123), HEX (0x7B), BIN (01111011b / 0b01111011). Адрес регистра — с нуля. LRC рассчитывается автоматически.'
        self.command_hint.set(hint)

    def build_request(self):
        values = {key: var.get() for key, var in self.command_fields.items()}
        try:
            if self.editor_rtc:
                from commands import integer
                frames = request_frames({'action': 'rtc'}, integer(values['address'], 'Адрес устройства'))
            else:
                fc = int(values['function'].split(' ')[0], 16)
                frames = [build_command(values['address'], fc, values['start'], values['count'], values['data'])]
        except Exception:
            self.request_text.configure(state='normal')
            self.request_text.delete('1.0', 'end')
            self.request_text.configure(state='disabled')
            raise
        self.request_text.configure(state='normal')
        self.request_text.delete('1.0', 'end')
        self.request_text.insert('1.0', '\n'.join(ascii_encode(data).decode().rstrip('\r\n') for data in frames))
        self.request_text.configure(state='disabled')
        return frames

    def connect(self):
        def action():
            if self.busy:
                raise ValueError('Дождитесь завершения обмена')
            if self.port:
                self.port.close()
                self.port = None
                self.ram_verified = False
                self.connect_button.configure(text='Подключить')
                self.status.set('Отключено')
                self.update_controls()
                return
            try:
                import serial
            except ImportError:
                raise RuntimeError('Установите pyserial: python -m pip install -r requirements.txt') from None
            s = self.settings_value()
            session = copy.deepcopy(DEFAULT_SESSION)
            port = serial.Serial(s['port'], s['baudrate'], bytesize=8, parity=s['parity'], stopbits=s['stopbits'], timeout=.05, write_timeout=s['timeout'])
            try:
                log = ExchangeLog(ROOT/'logs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f_connection'), session, s)
            except Exception:
                port.close()
                raise
            self.port, self.settings, self.log = port, s, log
            self.ram_verified = False
            self.connect_button.configure(text='Отключить')
            self.active_spec = {}
            def initialize(t):
                actual = read_device_session(t, s['address'])
                actual['continuous_receive'] = False
                log.set_session(actual)
                save_json(ROOT/'session.json', actual)
                self.events.put(('session', (actual, ROOT/'session.json')))
            self.run_job(initialize)
        self.guard(action)
        self.update_controls()

    def run_job(self, fn):
        if self.busy or self.port is None or not self.port.is_open:
            raise ValueError('Подключите прибор и дождитесь завершения текущей операции')
        self.busy = True
        self.update_controls()
        self.status.set('Обмен с прибором…')
        transport = Transport(self.port, self.settings['timeout'], self.log)
        def sent(request):
            if line_enabled(self.active_spec, 'status'):
                description = describe_command(request)
                if self.active_spec and not self.active_spec.get('action') and request in request_frames(self.active_spec, request[0]):
                    description += ' — '+self.active_spec.get('title', '')
                self.events.put(('text', description))
        transport.on_sent = sent
        def worker():
            try:
                fn(transport)
                self.events.put(('done', '//Операция завершена'))
            except Exception as exc:
                from serial import SerialException
                self.events.put(('disconnected' if isinstance(exc, (SerialException, OSError)) else 'error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def request_spec(self, frames):
        selected = self.requests[self.request_choice.current()]
        if selected.get('action') or frames == request_frames(selected, frames[0][0]):
            return copy.deepcopy(selected)
        return {}

    def emit_response(self, transport, request, result, spec):
        for line in response_lines(request, transport, result, spec):
            self.events.put(('text', line))

    def exchange(self, transport, request):
        spec = self.active_spec
        transport.presentation_spec = spec
        if line_enabled(spec, 'tx_data'):
            self.events.put(('text', 'TX  '+ascii_encode(request).decode().rstrip('\r\n')))
        try:
            result = transport.transact(request)
        except Exception:
            if line_enabled(spec, 'rx_data') and transport.last_ascii:
                self.events.put(('text', 'RX  '+transport.last_ascii.rstrip('\r\n')))
            raise
        self.emit_response(transport, request, result, spec)
        return result

    def start_stream(self):
        requests = self.build_request()
        if len(requests) != 1 or requests[0][1] not in (1, 2, 3, 4):
            raise ValueError('Для потока нужен один запрос чтения FC01–FC04')
        if self.busy or self.port is None or not self.port.is_open:
            raise ValueError('Подключите прибор и дождитесь завершения операции')
        request = requests[0]
        spec = self.request_spec(requests)
        self.active_spec = spec
        settings = copy.deepcopy(self.settings)
        self.pending_requests = []
        self.after_stream_job = None
        self.plot.clear()
        self.stream_stop.clear()
        self.streaming = True
        def job(t):
            actual = read_device_session(t, request[0])
            actual.update(continuous_receive=True, request=spec or dict(function=request[1],
                start=int.from_bytes(request[2:4], 'big'), count=int.from_bytes(request[4:6], 'big')),
                started_at=datetime.now(timezone.utc).isoformat())
            directory = ROOT/'logs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f_continuous')
            stream_log = ExchangeLog(directory, actual, settings)
            stream_log.set_session(actual)
            previous_log = t.log
            t.log = stream_log
            t.presentation_spec = spec
            self.events.put(('session', (actual, directory/'session.json')))
            try:
                self.exchange(t, frame(request[0], 5, 9, 65280))
                first = self.exchange(t, request)
                self.events.put(('plot', (time.monotonic(), series_values(request, t.last_registers, first, spec))))
                while not self.stream_stop.is_set():
                    result = t.transact(request, send_request=False, stop=self.stream_stop)
                    if result is not None:
                        self.events.put(('plot', (time.monotonic(), series_values(request, t.last_registers, result, spec))))
                        self.emit_response(t, request, result, spec)
            finally:
                # FC05 отличается от повторяемого FC01–04: пропускаем остатки потока до ACK.
                try:
                    t.transact(frame(request[0], 5, 9, 0), ignore_functions=(1, 2, 3, 4))
                finally:
                    stream_log.write('stream_end')
                    t.log = previous_log
            if self.after_stream_job is not None:
                self.after_stream_job(t)
            if self.pending_requests:
                self.active_spec = self.pending_spec
            for pending in self.pending_requests:
                self.exchange(t, pending)
        self.run_job(job)

    def send_requests(self):
        frames = self.build_request()
        if not frames:
            raise ValueError('Запрос пуст')
        if any(q[1:6] == bytes.fromhex('050001FF00') for q in frames):
            if len(frames) != 1:
                raise ValueError('Сохранение во Flash выполняется отдельно, кнопкой «Сохранить во Flash»')
            self.save_flash()
            return
        if any(q[1] in (6, 16) or (q[1] == 5 and int.from_bytes(q[2:4], 'big') in (4, 5, 6, 7, 8)) for q in frames):
            self.ram_verified = False
        if self.streaming:
            self.pending_spec = self.request_spec(frames)
            self.pending_requests = frames
            self.stream_stop.set()
            return
        self.active_spec = self.request_spec(frames)
        snapshot = self.session_value()
        def job(t):
            t.log.write('session_snapshot', session=snapshot, configuration_status='Editor snapshot, not verified device state')
            for data in frames:
                self.exchange(t, data)
                if data[1] == 5 and 1 <= int.from_bytes(data[2:4], 'big') <= 5 and data[4:6] == b'\xff\x00' and data[2:4] != b'\x00\x04':
                    t.wait_command(data[0], int.from_bytes(data[2:4], 'big'))
        self.run_job(job)

    def configure_device(self):
        s = self.session_value()
        address = self.settings['address']
        frames = configuration_frames(s, address)
        self.ram_verified = False
        def job(t):
            t.log.write('configuration_begin', session=s)
            try:
                for data in frames:
                    self.exchange(t, data)
                actual = t.transact(frame(address, 3, 0, 27))
                expected = frames[1][7:-2]
                if list(actual.values()) != [int.from_bytes(expected[i:i+2], 'big') for i in range(0, len(expected), 2)]:
                    raise ValueError('Проверка настроек чтением не прошла')
                mask = t.transact(frame(address, 3, 0x1d, 1))['001D']
                if mask != s['channel_mask']:
                    raise ValueError('Проверка выбранных каналов не прошла')
                bits = int(t.transact(frame(address, 1, 6, 3))['bits_hex'], 16) & 7
                wanted = int(s['filters']['median_enabled']) | int(s['filters']['average_enabled']) << 1 | int(s['calibration_enabled']) << 2
                if bits != wanted:
                    raise ValueError('Проверка флагов не прошла')
                t.log.write('configuration_applied', session=s, flash_saved=False)
                self.events.put(('ram_verified', None))
            except Exception as exc:
                t.log.write('configuration_failed', message=str(exc), state='Possibly partially applied; read device settings')
                raise RuntimeError(f'{exc}. Конфигурация могла примениться частично; прочитайте настройки прибора.') from exc
        if self.streaming:
            self.pending_requests = []
            self.after_stream_job = job
            self.streaming = False
            self.stream_stop.set()
            self.status.set('Остановка потока → STOP → отправка конфигурации…')
            self.update_controls()
        else:
            self.run_job(job)

    def save_flash(self):
        if not self.ram_verified or self.busy or self.port is None or not self.port.is_open:
            raise ValueError('Сначала отправьте конфигурацию в RAM и дождитесь успешной проверки')
        address = self.settings['address']
        self.active_spec = {}
        def prepare(t):
            actual = read_device_session(t, address)
            self.events.put(('flash_preview', actual))
        self.run_job(prepare)

    def confirm_flash(self, actual):
        f = actual['filters']
        mask = actual.get('channel_mask', (1 << actual['channel_count'])-1)
        lines = ['Будет перезаписана постоянная конфигурация прибора.',
                 'Измерения будут остановлены. Не отключайте питание до завершения.',
                 'Ниже настройки, прочитанные из RAM; несохранённые правки редактора не включены.', '',
                 'Выбраны: '+', '.join(f'T{i}' for i in range(8) if mask & (1 << i)),
                 f"Медиана: {f['median_enabled']}, окно {f['median_window']}; среднее: {f['average_enabled']}, окно {f['average_window']}",
                 f"Калибровка: {actual['calibration_enabled']}",
                 'Канал      Rref ×10       b (АЦП)       k ×100']
        for i, row in enumerate(actual['channels']):
            lines.append(f"T{i}:       {row['rref_x10']}          {row['b']}          {row['k_x100']}")
        lines.extend(['', 'Сохранить эти данные во Flash?'])
        if not messagebox.askyesno('Подтверждение записи во Flash', '\n'.join(lines), icon='warning', default='no', parent=self):
            self.status.set('Сохранение во Flash отменено')
            return
        address = self.settings['address']
        def save(t):
            self.exchange(t, frame(address, 5, 0, 0))
            current = read_device_session(t, address)
            if current != actual:
                raise ValueError('Настройки RAM изменились после подтверждения. Повторите сохранение.')
            self.exchange(t, frame(address, 5, 1, 65280))
            t.wait_command(address, 1)
            t.log.write('flash_saved', session=current)
            self.events.put(('text', '// Конфигурация сохранена во Flash'))
        self.run_job(save)

    def clear_output(self):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def poll_events(self):
        for _ in range(200):
            if self.events.empty():
                break
            kind, text = self.events.get_nowait()
            if kind == 'ram_verified':
                self.ram_verified = True
                continue
            if kind == 'flash_preview':
                self.pending_flash_preview = text
                continue
            if kind == 'plot':
                self.plot.add(*text)
                continue
            if kind == 'session':
                actual, path = text
                self.session_path = path
                # Поля были заблокированы на время чтения; временно разрешаем обновить JSON.
                self.metadata.configure(state='normal')
                self.show_session(actual)
                self.update_controls()
                continue
            self.output.configure(state='normal')
            if kind != 'done' or line_enabled(self.active_spec, 'status'):
                self.output.insert('end', text+'\n')
            if int(self.output.index('end-1c').split('.')[0]) > 1000:
                self.output.delete('1.0', '201.0')
            self.output.see('end')
            self.output.configure(state='disabled')
            if kind in ('done', 'error', 'disconnected'):
                self.busy = False
                self.streaming = False
                if kind != 'done':
                    self.ram_verified = False
                    self.pending_flash_preview = None
                self.status.set(text)
                if kind == 'disconnected':
                    try:
                        self.port.close()
                    except OSError:
                        pass
                    self.port = None
                    self.connect_button.configure(text='Подключить')
                self.update_controls()
                if kind in ('error', 'disconnected'):
                    messagebox.showerror('Обмен не завершён', text, parent=self)
                if kind == 'done' and self.pending_flash_preview is not None:
                    actual = self.pending_flash_preview
                    self.pending_flash_preview = None
                    self.guard(lambda: self.confirm_flash(actual))
        if self.plot.dirty:
            self.plot.redraw()
        self.update_controls()
        self.after(80, self.poll_events)

    def export(self):
        source = filedialog.askopenfilename(initialdir=ROOT/'logs', filetypes=[('Лог', '*.log')])
        if not source:
            return
        destination = filedialog.asksaveasfilename(initialfile=Path(source).stem+'.txt', defaultextension='.txt')
        if destination:
            if Path(source).resolve() == Path(destination).resolve():
                raise ValueError('Выберите отдельный файл для экспорта')
            count = export_log(source, destination)
            self.status.set(f'Экспортировано строк: {count} → {destination}')

    def close(self):
        if self.busy:
            messagebox.showinfo('Обмен', 'Дождитесь окончания текущей операции.', parent=self)
            return
        if self.port:
            self.port.close()
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
