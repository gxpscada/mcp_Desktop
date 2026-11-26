"""Bounded live numeric chart, drawn on Tk's canvas."""
import math
import csv
import json
import tempfile
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class TemperaturePlot(ttk.Frame):
    colors = ('#1976d2', '#d84315', '#388e3c', '#8e24aa', '#00838f', '#ad1457', '#6d4c41', '#455a64')

    def __init__(self, parent):
        super().__init__(parent)
        self.points = deque(maxlen=2000)
        # Полная история на диске: лимит графика не обрезает CSV.
        self.history = tempfile.TemporaryFile(mode='w+t', encoding='utf-8')
        self.bind('<Destroy>', self._close_history, add='+')
        self.origin = None
        self.dirty = True
        self.selected = []
        self.names = []
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        self.series_bar = ttk.Frame(self)
        self.series_bar.pack(fill='x')
        self.group = tk.StringVar(value='Все')
        self.group_choice = ttk.Combobox(bar, textvariable=self.group, state='readonly',
            values=('Все', 'Температура', 'АЦП', 'Сопротивление', 'Регистры / флаги'))
        self.group_choice.pack(side='left')
        self.group_choice.bind('<<ComboboxSelected>>', lambda _: self.redraw())
        ttk.Button(bar, text='Очистить', command=self.clear).pack(side='right')
        ttk.Button(bar, text='Экспорт CSV', command=self.export_dialog).pack(side='right')
        ttk.Label(self, text='Значения • время приёма на ПК, с • последние 2000 ответов; пропуски — разрывы').pack(anchor='w')
        self.canvas = tk.Canvas(self, background='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.redraw())

    def clear(self):
        self.points.clear()
        self.history.seek(0)
        self.history.truncate()
        self.origin = None
        self.names.clear()
        self.selected.clear()
        for child in self.series_bar.winfo_children(): child.destroy()
        self.redraw()

    def _close_history(self, event):
        if event.widget is self:
            self.history.close()

    def enabled_indices(self):
        return [i for i, var in enumerate(self.selected) if var.get() and
                (self.group.get() == 'Все' or self.category(self.names[i]) == self.group.get())]

    def export_csv(self, path):
        names = [self.names[i] for i in self.enabled_indices()]
        if not self.points or not names:
            raise ValueError('Нет данных или не выбраны ряды графика')
        count = 0
        self.history.seek(0)
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as output:
                writer = csv.writer(output)
                writer.writerow(['time_s', *names])
                for line in self.history:
                    timestamp, values = json.loads(line)
                    writer.writerow([format(timestamp, '.9f'),
                                     *[int(values[name]) if isinstance(values.get(name), bool)
                                       else values.get(name) for name in names]])
                    count += 1
        finally:
            self.history.seek(0, 2)
        return count

    def export_dialog(self):
        if not self.points or not self.enabled_indices():
            messagebox.showinfo('Экспорт CSV', 'Нет данных или не выбраны ряды графика', parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, title='Сохранить выбранные графики за весь сеанс',
            initialfile='graph.csv', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            count = self.export_csv(path)
            messagebox.showinfo('Экспорт CSV', f'Сохранено строк: {count}\nВремя — секунды от первой точки.\n{path}', parent=self)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Экспорт CSV', str(exc), parent=self)

    @staticmethod
    def category(name):
        if name.startswith('T') and '_C' in name: return 'Температура'
        if name.startswith('ADC'): return 'АЦП'
        if name.startswith('R') and '_ohm' in name: return 'Сопротивление'
        return 'Регистры / флаги'

    def add(self, timestamp, values):
        values = dict(values)
        for key in list(values):
            if key.endswith('_C_float'):
                values[key[:-6]] = values.pop(key)
        values = {k: v for k, v in values.items() if v is None or type(v) in (int, float, bool)}
        if not values:
            return
        if self.origin is None:
            self.origin = timestamp
        for name in values:
            if name not in self.names:
                self.names.append(name)
                var = tk.BooleanVar(value=True)
                self.selected.append(var)
                i = len(self.names)-1
                ttk.Checkbutton(self.series_bar, text=name, variable=var, command=self.redraw).grid(row=i//8, column=i%8, sticky='w')
                for _, row in self.points: row.append(None)
        row = []
        for name in self.names:
            value = values.get(name)
            row.append(value if isinstance(value, (int, float)) and math.isfinite(value) else None)
        self.points.append((timestamp-self.origin, row))
        self.history.write(json.dumps([timestamp-self.origin, dict(zip(self.names, row))],
                                      ensure_ascii=False, allow_nan=False)+'\n')
        self.dirty = True

    def redraw(self):
        self.dirty = False
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        left, top, right, bottom = 65, 25, w-20, h-45
        enabled = self.enabled_indices()
        values = [row[i] for _, row in self.points for i in enabled if row[i] is not None]
        if not values:
            c.create_text(w/2, h/2, text='Ожидание данных выбранной группы', fill='#555555')
            return
        ymin, ymax = min(values), max(values)
        margin = max((ymax-ymin)*.1, .1)
        ymin -= margin; ymax += margin
        xmin, xmax = self.points[0][0], max(self.points[-1][0], self.points[0][0]+1)
        def xy(t, v):
            return left+(t-xmin)/(xmax-xmin)*(right-left), bottom-(v-ymin)/(ymax-ymin)*(bottom-top)
        for n in range(6):
            v = ymin+(ymax-ymin)*n/5
            y = xy(xmin, v)[1]
            c.create_line(left, y, right, y, fill='#e5e7eb')
            c.create_text(left-7, y, text=f'{v:.2f}', anchor='e')
            t = xmin+(xmax-xmin)*n/5
            x = xy(t, ymin)[0]
            c.create_text(x, bottom+15, text=f'{t:.1f}')
        for i in enabled:
            segment = []
            def draw():
                if len(segment) >= 4:
                    c.create_line(*segment, fill=self.colors[i % len(self.colors)], width=2)
                elif segment:
                    x, y = segment
                    c.create_oval(x-2, y-2, x+2, y+2, fill=self.colors[i % len(self.colors)], outline='')
            for t, row in self.points:
                if row[i] is None:
                    draw(); segment = []
                else:
                    segment.extend(xy(t, row[i]))
            draw()
            c.create_text(left+i*70, 12, text=self.names[i], fill=self.colors[i % len(self.colors)], anchor='w')
