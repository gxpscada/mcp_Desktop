"""Request-specific display settings; never change the original measurements."""
import math
import json

LINE_NAMES = ('tx_data', 'rx_data', 'modbus_field_type', 'json', 'human', 'status')


def validate_view(request):
    view = request.get('view', {})
    for name, enabled in view.get('lines', {}).items():
        if name not in LINE_NAMES or type(enabled) is not bool:
            raise ValueError('view.lines: нужны известные имена и true/false')
    titles = set()
    for item in view.get('values', []):
        title, value = item['title'], item['value']
        if not isinstance(title, str) or not title or title in titles:
            raise ValueError('view.values: названия должны быть непустыми и уникальными')
        titles.add(title)
        if value.get('notation', 'DEC') not in ('DEC', 'HEX', 'BIN'):
            raise ValueError('notation: DEC, HEX или BIN')
        if not isinstance(value.get('gain', 1), (int, float)) or not math.isfinite(value.get('gain', 1)):
            raise ValueError('gain должен быть конечным числом')
        if type(value.get('precision', 2)) is not int or not 0 <= value.get('precision', 2) <= 12:
            raise ValueError('precision: целое число 0…12')
    export = view.get('export_format', {})
    if type(export.get('first_line_titles', True)) is not bool:
        raise ValueError('first_line_titles должен быть true/false')
    if not isinstance(export.get('separator', '\t'), str) or len(export.get('separator', '\t')) != 1:
        raise ValueError('separator должен быть одним символом')


def line_enabled(spec, name):
    return spec.get('view', {}).get('lines', {}).get(name, True)


def series_values(request, registers, decoded, spec):
    """Prefer physical quantities; never chart the two halves of a float or RTC."""
    fc = request[1]
    start = int.from_bytes(request[2:4], 'big')
    custom = spec.get('view', {}).get('values', [])
    if custom and fc in (1, 2, 3, 4):
        result = {}
        for i, item in enumerate(custom):
            a = start+i
            raw = registers.get(f'{a:04X}', {}).get('DEC')
            if raw is not None and fc == 4:
                if 0x15 <= a <= 0x1c:
                    raw = None if raw == 0x8000 else (raw if raw < 32768 else raw-65536)
                elif 0x20 <= a <= 0x27 or 0x68 <= a <= 0x6f:
                    raw = None if raw == 65535 else raw
            result[item['title']] = None if raw is None else round(raw*item['value'].get('gain', 1), item['value'].get('precision', 2))
        return result
    if fc not in (1, 2, 3, 4):
        return {}
    result = {}
    for key, value in decoded.items():
        if key.startswith(('T', 'ADC', 'R')) and (value is None or isinstance(value, (int, float))):
            result[key] = value
    for key, reg in registers.items():
        a = int(key, 16)
        if fc == 4 and (0x15 <= a <= 0x27 or 0x40 <= a <= 0x5f):
            continue
        value = reg['DEC']
        if fc == 4 and 0x68 <= a <= 0x6f:
            result[f'ADC{a-0x68}_Q1'] = None if value == 65535 else value
        else:
            result[key] = value
    return result


def response_lines(request, transport, decoded, spec):
    lines = []
    if line_enabled(spec, 'rx_data') and transport.last_ascii:
        lines.append('RX  '+transport.last_ascii.rstrip('\r\n'))
    prefix = transport.last_space if line_enabled(spec, 'modbus_field_type') else ''
    if line_enabled(spec, 'json'):
        lines.append((prefix+' ' if prefix else '')+json.dumps(transport.last_registers, ensure_ascii=False))
    elif prefix:
        lines.append(prefix)
    if line_enabled(spec, 'human'):
        values = series_values(request, transport.last_registers, decoded, spec)
        for item in spec.get('view', {}).get('values', []):
            key, config = item['title'], item['value']
            value = values.get(key)
            if value is not None:
                notation = config.get('notation', 'DEC')
                values[key] = (hex(int(value)) if notation == 'HEX' else bin(int(value)) if notation == 'BIN'
                               else f"{value:.{config.get('precision', 2)}f}")
        if values:
            lines.append('//Значения '+json.dumps(values, ensure_ascii=False))
    return lines
