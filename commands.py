"""Named command fields and descriptions shared by the editor and transport."""
import re
from core import frame, parse_rtu_frame

FUNCTIONS = {1: 'Чтение COILS', 2: 'Чтение DI', 3: 'Чтение HR', 4: 'Чтение IR',
             5: 'Запись COIL', 6: 'Запись HR', 16: 'Запись нескольких HR'}


def integer(text, name):
    text = text.strip()
    try:
        if text.lower().startswith('0x'):
            value = int(text, 16)
        elif text.lower().startswith('0b'):
            digits = text[2:]
            if not re.fullmatch('[01]+', digits):
                raise ValueError()
            value = int(digits, 2)
        elif text.lower().endswith('b'):
            digits = text[:-1]
            if not re.fullmatch('[01]+', digits):
                raise ValueError()
            value = int(digits, 2)
        else:
            value = int(text, 10)
    except ValueError:
        raise ValueError(f'{name}: введите DEC, HEX (0x7B) или BIN (01111011b / 0b01111011)') from None
    if not 0 <= value <= 65535:
        raise ValueError(f'{name}: допустимо 0…65535')
    return value


def build_command(address, function, start, count, data):
    address = integer(address, 'Адрес устройства')
    start = integer(start, 'Начальный адрес')
    if function in (1, 2, 3, 4):
        values = integer(count, 'Количество')
    elif function in (5, 6):
        values = integer(data, 'Данные')
    elif function == 16:
        values = [integer(part, 'Данные') for part in re.split(r'[\s,;]+', data.strip()) if part]
        if not 1 <= len(values) <= 123:
            raise ValueError('FC16: введите от 1 до 123 значений')
        if start+len(values) > 65536:
            raise ValueError('Диапазон выходит за адрес 0xFFFF')
    else:
        raise ValueError('Выберите поддерживаемую функцию')
    result = frame(address, function, start, values)
    return parse_rtu_frame(result.hex())


def describe_command(request, title=None):
    if title:
        text = title.strip()
    else:
        text = _describe_command(request)[2:]
    return '//'+text[:1].upper()+text[1:]


def _describe_command(request):
    fc = request[1]
    start = int.from_bytes(request[2:4], 'big')
    value = int.from_bytes(request[4:6], 'big')
    if fc == 5:
        names = {0: 'записать start' if value else 'записать stop',
                 1: 'сохранить во flash', 2: 'сбросить ошибки', 3: 'установить rtc',
                 4: 'перезагрузить прибор', 5: 'заводские настройки',
                 9: 'включить непрерывную передачу' if value else 'остановить непрерывную передачу'}
        if start in (6, 7, 8):
            return '//'+('включить ' if value else 'выключить ')+{6: 'медианный фильтр', 7: 'усреднение', 8: 'калибровку'}[start]
        if start in names and (value or start in (0, 9)):
            return '//'+names[start]
    if fc in (1, 2, 3, 4):
        names = {(1, 0): 'start/stop', (1, 6): 'флаги фильтров и калибровки',
                 (1, 7): 'усреднение', (1, 8): 'калибровку',
                 (3, 0): 'конфигурацию', (3, 1): 'rref', (3, 9): 'b', (3, 17): 'k',
                 (3, 25): 'окно усреднения', (3, 26): 'окно медианы', (3, 29): 'маску каналов',
                 (4, 9): 'статусы датчиков', (4, 21): 'температуры', (4, 30): 'rtc',
                 (4, 32): 'ацп', (4, 64): 'температуры float32', (4, 80): 'сопротивления',
                 (4, 96): 'программные статусы', (4, 104): 'ацп q1'}
        name = names.get((fc, start), {1: 'coils', 2: 'di', 3: 'hr', 4: 'ir'}[fc])
        if fc == 4 and start == 21 and value == 19:
            name = 'температуры, rtc и ацп'
        return '//читать '+name
    if fc in (6, 16):
        return '//записать '+{0: 'конфигурацию', 27: 'время rtc', 29: 'маску каналов'}.get(start, 'регистры hr')
    return '//записать coil'
