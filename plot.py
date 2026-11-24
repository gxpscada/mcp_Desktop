"""Bounded live temperature chart, drawn on Tk's canvas."""
import math
from collections import deque
import tkinter as tk
from tkinter import ttk


class TemperaturePlot(ttk.Frame):
    colors = ('#1976d2', '#d84315', '#388e3c', '#8e24aa', '#00838f', '#ad1457', '#6d4c41', '#455a64')

    def __init__(self, parent):
        super().__init__(parent)
        self.points = deque(maxlen=2000)
        self.origin = None
        self.dirty = True
        self.selected = [tk.BooleanVar(value=True) for _ in range(8)]
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        for i, var in enumerate(self.selected):
            ttk.Checkbutton(bar, text=f'T{i}', variable=var, command=self.redraw).pack(side='left')
        ttk.Button(bar, text='Очистить', command=self.clear).pack(side='right')
        ttk.Label(self, text='Температура, °C • время приёма на ПК, с • последние 2000 ответов; ошибки — разрывы').pack(anchor='w')
        self.canvas = tk.Canvas(self, background='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.redraw())

    def clear(self):
        self.points.clear()
        self.origin = None
        self.redraw()

    def add(self, timestamp, values):
        if not any(f'T{i}_C' in values or f'T{i}_C_float' in values for i in range(8)):
            return
        if self.origin is None:
            self.origin = timestamp
        row = []
        for i in range(8):
            value = values.get(f'T{i}_C_float', values.get(f'T{i}_C'))
            row.append(value if isinstance(value, (int, float)) and math.isfinite(value) else None)
        self.points.append((timestamp-self.origin, row))
        self.dirty = True

    def redraw(self):
        self.dirty = False
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        left, top, right, bottom = 65, 25, w-20, h-45
        enabled = [i for i, var in enumerate(self.selected) if var.get()]
        values = [row[i] for _, row in self.points for i in enabled if row[i] is not None]
        if not values:
            c.create_text(w/2, h/2, text='Ожидание достоверных температур из потока', fill='#555555')
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
                    c.create_line(*segment, fill=self.colors[i], width=2)
                elif segment:
                    x, y = segment
                    c.create_oval(x-2, y-2, x+2, y+2, fill=self.colors[i], outline='')
            for t, row in self.points:
                if row[i] is None:
                    draw(); segment = []
                else:
                    segment.extend(xy(t, row[i]))
            draw()
            c.create_text(left+i*70, 12, text=f'T{i}', fill=self.colors[i], anchor='w')
