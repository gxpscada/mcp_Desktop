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


def describe_command(request):
    fc = request[1]
    start = int.from_bytes(request[2:4], 'big')
    value = int.from_bytes(request[4:6], 'big')
    action = FUNCTIONS.get(fc, f'FC{fc:02X}')
    if fc == 5:
        names = {0: 'START' if value else 'STOP', 1: 'Сохранить во Flash',
                 2: 'Сбросить ошибки', 3: 'Установить RTC',
                 4: 'Перезагрузить прибор', 5: 'Заводские настройки',
                 9: 'Включить непрерывную передачу' if value else 'Остановить непрерывную передачу'}
        action = names.get(start, action) if value or start in (0, 9) else action
    prefix = f'// Команда: {action}; устройство {request[0]}; FC=0x{fc:02X}; адрес=0x{start:04X}'
    if fc in (1, 2, 3, 4):
        return prefix+f'; количество={value}'
    if fc == 16:
        values = [int.from_bytes(request[i:i+2], 'big') for i in range(7, len(request)-2, 2)]
        return prefix+f'; количество={value}; данные='+', '.join(f'0x{v:04X}' for v in values)
    return prefix+f'; данные=0x{value:04X} ({value})'
