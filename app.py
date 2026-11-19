"""Russian desktop GUI for stage one. Run: python app.py."""
import copy
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
from core import (DEFAULT_APP, DEFAULT_SESSION, DEFAULT_REQUESTS, read_json, save_json,
                  validate_session, configuration_frames, request_frames, parse_frame,
                  ExchangeLog, Transport, export_log, frame)

ROOT = Path(__file__).resolve().parent


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Температуры • PT100 | Этап 1')
        self.geometry('1140x850')
        self.minsize(960, 740)
        self.port = None
        self.log = None
        self.busy = False
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
        ttk.Label(outer, text='PT100 / MAX31865   •   Настройка и одиночный обмен   •   Modbus RTU').pack(anchor='w', pady=(2, 14))
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
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=8)
        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        config = ttk.Frame(tabs, padding=12)
        requests = ttk.Frame(tabs, padding=12)
        tabs.add(config, text='  Сеанс и калибровка  ')
        tabs.add(requests, text='  Запросы и ответы  ')
        bar = ttk.Frame(config)
        bar.pack(fill='x')
        for text, action in [('Открыть JSON', self.load_session), ('Сохранить как…', self.save_session),
                             ('Добавить поле', self.add_field), ('Отправить конфигурацию', self.configure_device)]:
            ttk.Button(bar, text=text, command=lambda fn=action: self.guard(fn)).pack(side='left', padx=(0, 6))
        self.session_label = tk.StringVar(value=str(self.session_path.name))
        ttk.Label(config, textvariable=self.session_label).pack(anchor='w', pady=8)
        options = ttk.Frame(config)
        options.pack(fill='x')
        self.count = tk.StringVar(value='3')
        ttk.Label(options, text='Активных каналов').pack(side='left')
        ttk.Spinbox(options, from_=1, to=8, width=4, textvariable=self.count).pack(side='left', padx=8)
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
            ttk.Label(table, text=f'T{i}').grid(row=i+1, column=0, padx=10)
            row = {}
            for col, key in enumerate(('rref_x10', 'b', 'k_x100'), 1):
                row[key] = tk.StringVar(value=str(self.session['channels'][i][key]))
                ttk.Entry(table, textvariable=row[key], width=20).grid(row=i+1, column=col, padx=10, pady=2)
            self.channels.append(row)
        ttk.Label(config, text='Фильтры общие. b применяется к АЦП до CVD; k — к температуре. Число каналов ограничено сборкой платы.').pack(anchor='w')
        ttk.Label(config, text='Дополнительные поля сеанса (JSON; сохраняются на ПК)').pack(anchor='w', pady=(12, 4))
        self.metadata = tk.Text(config, height=4, font=('Consolas', 10), wrap='word')
        self.metadata.pack(fill='both', expand=True)
        self.metadata.insert('1.0', '{}')
        self.flash = tk.BooleanVar(value=False)
        ttk.Checkbutton(config, text='После отправки сохранить во Flash (с ожиданием завершения)', variable=self.flash).pack(anchor='w', pady=6)
        ttk.Label(config, text='Отправка: STOP → настройки → флаги → проверка чтением. Измерение остаётся остановленным.').pack(anchor='w')
        request_bar = ttk.Frame(requests)
        request_bar.pack(fill='x')
        self.request_choice = ttk.Combobox(request_bar, state='readonly', width=43)
        self.request_choice.pack(side='left', padx=(0, 8))
        self.request_choice.bind('<<ComboboxSelected>>', lambda e: self.guard(self.preview_request))
        ttk.Button(request_bar, text='Reload requests.json', command=lambda: self.guard(self.reload_requests)).pack(side='left')
        ttk.Button(request_bar, text='Сформировать', command=lambda: self.guard(self.preview_request)).pack(side='left', padx=8)
        ttk.Label(requests, text='Полные RTU-кадры HEX, включая CRC (Lo Hi). Один кадр на строку; содержимое можно менять.').pack(anchor='w', pady=(14, 6))
        self.request_text = tk.Text(requests, height=7, font=('Consolas', 12), wrap='word')
        self.request_text.pack(fill='x')
        ttk.Button(requests, text='Отправить кадры', command=lambda: self.guard(self.send_requests)).pack(anchor='w', pady=10)
        ttk.Label(requests, text='Ответы и события').pack(anchor='w')
        self.output = tk.Text(requests, height=12, font=('Consolas', 10), state='disabled', wrap='word')
        self.output.pack(fill='both', expand=True)
        footer = ttk.Frame(outer)
        footer.pack(fill='x', pady=(12, 0))
        ttk.Button(footer, text='Экспорт .log → .txt', command=lambda: self.guard(self.export)).pack(side='left')
        ttk.Label(footer, text='Лог создаётся при подключении • JSON сеанса в заголовке • пропуски = NA').pack(side='left', padx=12)
        self.reload_requests()
        if self.session_path.exists():
            self.show_session(validate_session(read_json(self.session_path)))
        self.after(80, self.poll_events)

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
        return dict(s, bytesize=8, schema_version=1)

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
        s.update(channel_count=int(self.count.get()), calibration_enabled=self.calibration.get(),
                 channels=[{key: int(var.get()) for key, var in c.items()} for c in self.channels],
                 metadata=json.loads(self.metadata.get('1.0', 'end')))
        s['filters'].update(median_enabled=self.median.get(), average_enabled=self.average.get(),
                            median_window=int(self.median_window.get()), average_window=int(self.average_window.get()))
        return validate_session(s)

    def show_session(self, s):
        self.session = copy.deepcopy(s)
        self.count.set(str(s['channel_count']))
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
            if not isinstance(request['title'], str):
                raise ValueError('title должен быть строкой')
            for data in request_frames(request, 1):
                parse_frame(data.hex())
        self.requests = values
        self.request_choice['values'] = [r['title'] for r in values]
        self.request_choice.current(0)
        self.preview_request()

    def preview_request(self):
        address = self.settings['address'] if self.port else self.settings_value()['address']
        frames = request_frames(self.requests[self.request_choice.current()], address)
        self.request_text.delete('1.0', 'end')
        self.request_text.insert('1.0', '\n'.join(data.hex(' ').upper() for data in frames))

    def connect(self):
        def action():
            if self.busy:
                raise ValueError('Дождитесь завершения обмена')
            if self.port:
                self.port.close()
                self.port = None
                self.connect_button.configure(text='Подключить')
                self.status.set('Отключено')
                return
            try:
                import serial
            except ImportError:
                raise RuntimeError('Установите pyserial: python -m pip install -r requirements.txt') from None
            s = self.settings_value()
            session = self.session_value()
            port = serial.Serial(s['port'], s['baudrate'], bytesize=8, parity=s['parity'], stopbits=s['stopbits'], timeout=.05, write_timeout=s['timeout'])
            try:
                log = ExchangeLog(ROOT/'logs', session, s)
            except Exception:
                port.close()
                raise
            self.port, self.settings, self.log = port, s, log
            self.connect_button.configure(text='Отключить')
            self.status.set(f"Подключено: {s['port']} • адрес {s['address']} • {log.path.name}")
        self.guard(action)

    def run_job(self, fn):
        if self.busy or self.port is None:
            raise ValueError('Подключите прибор и дождитесь завершения текущей операции')
        self.busy = True
        self.status.set('Обмен с прибором…')
        transport = Transport(self.port, self.settings['timeout'], self.log)
        def worker():
            try:
                fn(transport)
                self.events.put(('done', 'Операция завершена'))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def exchange(self, transport, request):
        self.events.put(('text', 'TX  '+request.hex(' ').upper()))
        result = transport.transact(request)
        self.events.put(('text', 'RX  '+json.dumps(result, ensure_ascii=False)))
        return result

    def send_requests(self):
        frames = [parse_frame(line) for line in self.request_text.get('1.0', 'end').splitlines() if line.strip()]
        if not frames:
            raise ValueError('Запрос пуст')
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
        flash = self.flash.get()
        def job(t):
            t.log.write('configuration_begin', session=s)
            try:
                for data in frames:
                    self.exchange(t, data)
                actual = t.transact(frame(address, 3, 0, 27))
                expected = frames[1][7:-2]
                if list(actual.values()) != [int.from_bytes(expected[i:i+2], 'big') for i in range(0, len(expected), 2)]:
                    raise ValueError('Проверка настроек чтением не прошла')
                bits = int(t.transact(frame(address, 1, 6, 3))['bits_hex'], 16) & 7
                wanted = int(s['filters']['median_enabled']) | int(s['filters']['average_enabled']) << 1 | int(s['calibration_enabled']) << 2
                if bits != wanted:
                    raise ValueError('Проверка флагов не прошла')
                if flash:
                    self.exchange(t, frame(address, 5, 1, 65280))
                    t.wait_command(address, 1)
                t.log.write('configuration_applied', session=s, flash_saved=flash)
            except Exception as exc:
                t.log.write('configuration_failed', message=str(exc), state='Possibly partially applied; read device settings')
                raise RuntimeError(f'{exc}. Конфигурация могла примениться частично; прочитайте настройки прибора.') from exc
        self.run_job(job)

    def poll_events(self):
        while not self.events.empty():
            kind, text = self.events.get_nowait()
            self.output.configure(state='normal')
            self.output.insert('end', text+'\n')
            self.output.see('end')
            self.output.configure(state='disabled')
            if kind in ('done', 'error'):
                self.busy = False
                self.status.set(text)
                if kind == 'error':
                    messagebox.showerror('Обмен не завершён', text, parent=self)
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
