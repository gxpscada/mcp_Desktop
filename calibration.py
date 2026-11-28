"""Fit the firmware's ADC offset and post-CVD temperature multiplier."""
import math


def number(text):
    value = float(str(text).strip().replace(',', '.'))
    if not math.isfinite(value):
        raise ValueError('Нужны конечные числовые значения')
    return value


def ratio(t):
    if not -200 <= t <= 850:
        raise ValueError('Температура должна быть в пределах −200…850 °C')
    return 1 + .0039083*t - .0000005775*t*t + (-4.183e-12*(t-100)*t**3 if t < 0 else 0)


def temperature(r):
    if not ratio(-200) <= r <= ratio(850):
        raise ValueError('Сопротивление вне диапазона CVD')
    lo, hi = -200., 850.
    for _ in range(48):
        mid = (lo+hi)/2
        if ratio(mid) < r: lo = mid
        else: hi = mid
    return (lo+hi)/2


def calculate(inputs, rref_x10):
    r0 = number(inputs['r0'])
    rref = number(rref_x10)/10
    if r0 not in (100, 1000) or not 0 < rref <= 6553.5:
        raise ValueError('Проверьте тип датчика и Rref ×10')
    scale = 32768*r0/rref
    points = []
    for suffix, reference in [('zero', 0.), ('fixed', 29.76), ('custom', None)]:
        adc, measured = inputs.get('adc_'+suffix, '').strip(), inputs.get('temp_'+suffix, '').strip()
        if not adc and not measured:
            continue
        if reference is None:
            reference = number(inputs.get('reference', ''))
        ratio(reference)
        raw = number(adc) if adc else scale*ratio(number(measured))
        if not 0 <= raw <= 32767:
            raise ValueError('АЦП: 0…32767; для Q1 сначала разделите значение на 2')
        points.append((reference, raw))
    if len(points) < 2 or len({t for t, _ in points}) != len(points):
        raise ValueError('Заполните минимум две разные точки эталона (АЦП или температуру)')
    points.sort()
    if any(b[1] <= a[1] for a, b in zip(points, points[1:])):
        raise ValueError('При росте температуры эталона показания должны увеличиваться')
    # Перебор именно представимых k×100; b округляется до целого отсчёта.
    # При наличии ледяной точки сохраняем её как опору для смещения.
    zero = next((raw for ref, raw in points if ref == 0), None)
    best = None
    for k100 in range(1, 65536):
        k = k100/100
        if any(not -200 <= ref/k <= 850 for ref, _ in points):
            continue
        offsets = [scale*ratio(ref/k)-raw for ref, raw in points]
        b = round(scale-zero if zero is not None else sum(offsets)/len(offsets))
        if not -32768 <= b <= 32767 or any(not 0 <= raw+b <= 32767 or
                not ratio(-200) <= (raw+b)/scale <= ratio(850) for _, raw in points):
            continue
        error = sum((offset-b)**2 for offset in offsets)
        if best is None or error < best[0]:
            best = (error, b, k100)
    if best is None:
        raise ValueError('Не удалось подобрать допустимые b и k; проверьте исходные точки')
    _, b, k100 = best
    errors = [temperature((raw+b)/scale)*(k100/100)-ref for ref, raw in points]
    return dict(b=b, k_x100=k100, max_error=max(abs(e) for e in errors), points=len(points))
