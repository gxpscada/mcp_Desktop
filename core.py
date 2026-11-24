"""PT100 configuration and Modbus ASCII over USB CDC.

The shared binary representation retains an internal CRC for existing validators;
only colon/HEX/LRC/CRLF is sent on the wire.
"""
import copy
import json
import math
import struct
import time
from datetime import datetime, timezone
from pathlib import Path


def crc16(data):
    crc = 0xffff
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def frame(address, function, start, values):
    if not 1 <= address <= 247:
        raise ValueError('Адрес прибора должен быть 1…247')
    if function in (1, 2, 3, 4, 5, 6):
        payload = struct.pack('>HH', start, values)
    elif function == 16:
        payload = struct.pack('>HHB', start, len(values), len(values)*2)
        payload += struct.pack('>'+'H'*len(values), *values)
    else:
        raise ValueError('Поддержаны FC01/02/03/04/05/06/10')
    data = bytes([address, function]) + payload
    return data + struct.pack('<H', crc16(data))


def parse_rtu_frame(text):
    data = bytes.fromhex(text.strip().lstrip(':'))
    if len(data) < 8 or len(data) > 256 or crc16(data):
        raise ValueError('Неверная длина кадра или CRC. CRC передаётся Lo, Hi.')
    if not 1 <= data[0] <= 247:
        raise ValueError('Нужен unicast-адрес 1…247')
    fc, start, count = data[1], *struct.unpack('>HH', data[2:6])
    if fc in (1, 2, 3, 4):
        if len(data) != 8 or not 1 <= count <= (2000 if fc < 3 else 125) or start+count > 65536:
            raise ValueError('Неверный диапазон чтения')
    elif fc in (5, 6):
        if len(data) != 8 or (fc == 5 and count not in (0, 0xff00)):
            raise ValueError('Неверная одиночная запись')
    elif fc == 16:
        if not 1 <= count <= 123 or len(data) != 9+count*2 or data[6] != count*2 or start+count > 65536:
            raise ValueError('Неверная групповая запись')
    else:
        raise ValueError('Функция не поддерживается редактором')
    return data


def ascii_encode(packet):
    """Internal binary ADU -> actual ASCII wire frame. Internal CRC is not sent."""
    body = bytes(packet[:-2])
    lrc = (-sum(body)) & 255
    return b':' + (body + bytes([lrc])).hex().upper().encode('ascii') + b'\r\n'


def ascii_decode(wire):
    if not 9 <= len(wire) <= 513 or not wire.startswith(b':') or not wire.endswith(b'\r\n'):
        raise ValueError('ASCII: нужен кадр :HEX+LRC с завершением CRLF')
    digits = wire[1:-2]
    if len(digits) % 2 or any(c not in b'0123456789ABCDEFabcdef' for c in digits):
        raise ValueError('ASCII: неверные HEX-символы или нечётная длина')
    decoded = bytes.fromhex(digits.decode('ascii'))
    if sum(decoded) & 255:
        raise ValueError('ASCII: неверная LRC')
    body = decoded[:-1]
    return body + struct.pack('<H', crc16(body))


def parse_frame(text):
    # Editor uses one line per frame; actual CRLF is appended at transmission.
    packet = ascii_decode(text.strip().encode('ascii') + b'\r\n')
    return parse_rtu_frame(packet.hex())


DEFAULT_APP = dict(protocol="modbus-ascii", schema_version=1, port='COM3', baudrate=921600, bytesize=8,
                   parity='N', stopbits=1, timeout=2.0, address=1)
DEFAULT_SESSION = dict(schema_version=1, profile='pt100-v2', channel_count=3,
    filters=dict(median_enabled=True, median_window=3, average_enabled=True, average_window=4),
    calibration_enabled=True,
    channels=[dict(rref_x10=43000, b=0, k_x100=100) for _ in range(8)], metadata={})
DEFAULT_REQUESTS = [
    dict(title='Все T + RTC + ADC', function=4, start=0x15, count=19),
    dict(title='Температуры T0–T7', function=4, start=0x15, count=8),
    dict(title='АЦП T0–T7', function=4, start=0x20, count=8),
    dict(title='Программные статусы T0–T7', function=4, start=0x60, count=8),
    dict(title='АЦП Q1 (полуотсчёты) T0–T7', function=4, start=0x68, count=8),
    dict(title='Температуры float32 T0–T7', function=4, start=0x40, count=16),
    dict(title='Маска выбранных каналов', function=3, start=0x1d, count=1),
    dict(title='Статусы T0–T7', function=4, start=9, count=8),
    dict(title='Настройки прибора', function=3, start=0, count=27),
    dict(title='Флаги фильтров и калибровки', function=1, start=6, count=3),
    dict(title='Версия платы и ПО', function=4, start=0x35, count=2),
    dict(title='START', function=5, start=0, value=65280),
    dict(title='STOP', function=5, start=0, value=0),
    dict(title='Установить RTC по времени ПК', action='rtc')]


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def validate_session(s):
    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Ожидается целое число {low}…{high}, получено {value!r}')
    if s['schema_version'] != 1 or s['profile'] != 'pt100-v2':
        raise ValueError('Неизвестная версия или профиль сеанса')
    integer(s['channel_count'], 1, 8)
    mask = s.get('channel_mask', (1 << s['channel_count']) - 1)
    integer(mask, 1, 255)
    if mask.bit_length() != s['channel_count']:
        raise ValueError('Граница каналов должна соответствовать старшему выбранному каналу')
    if len(s['channels']) != 8:
        raise ValueError('Требуются настройки всех восьми регистров каналов')
    for c in s['channels']:
        integer(c['rref_x10'], 1, 65535)
        integer(c['b'], -32768, 32767)
        integer(c['k_x100'], 1, 65535)
    f = s['filters']
    integer(f['median_window'], 3, 7)
    integer(f['average_window'], 2, 16)
    if f['median_window'] not in (3, 5, 7) or f['average_window'] not in (2, 4, 8, 16):
        raise ValueError('Недопустимое окно фильтра')
    for value in (f['median_enabled'], f['average_enabled'], s['calibration_enabled']):
        if type(value) is not bool:
            raise ValueError('Флаги должны быть true/false')
    if not isinstance(s['metadata'], dict):
        raise ValueError('metadata должен быть объектом JSON')
    json.dumps(s, allow_nan=False)
    return s


def configuration_frames(s, address):
    validate_session(s)
    channels = s['channels']
    regs = [s['channel_count']] + [c['rref_x10'] for c in channels]
    regs += [c['b'] & 65535 for c in channels] + [c['k_x100'] for c in channels]
    regs += [s['filters']['average_window'], s['filters']['median_window']]
    result = [frame(address, 5, 0, 0), frame(address, 16, 0, regs)]
    result.append(frame(address, 6, 0x1d, s.get('channel_mask', (1 << s['channel_count']) - 1)))
    for index, enabled in enumerate((s['filters']['median_enabled'], s['filters']['average_enabled'], s['calibration_enabled']), 6):
        result.append(frame(address, 5, index, 65280 if enabled else 0))
    return result


def request_frames(request, address):
    if request.get('action') == 'rtc':
        now = int(time.time())
        return [frame(address, 5, 0, 0), frame(address, 16, 27, [now & 65535, now >> 16]), frame(address, 5, 3, 65280)]
    return [frame(address, request['function'], request['start'],
                  request.get('values', request.get('value', request.get('count'))))]


def decode(request, response):
    fc = request[1]
    if fc in (3, 4):
        start = int.from_bytes(request[2:4], 'big')
        words = struct.unpack('>'+'H'*(response[2]//2), response[3:-2])
        result = {}
        for a, value in enumerate(words, start):
            key = f'{a:04X}'
            if fc == 4 and 0x15 <= a <= 0x1c:
                key = f'T{a-0x15}_C'
                value = None if value == 0x8000 else (value if value < 32768 else value-65536)/100
            elif fc == 4 and 0x20 <= a <= 0x27:
                key = f'ADC{a-0x20}'
                value = None if value == 65535 else value
            result[key] = value
        if fc == 4:
            for base, label in ((0x40, 'T'), (0x50, 'R')):
                for i in range(8):
                    a = base + 2*i
                    if start <= a and a+1 < start+len(words):
                        value = struct.unpack('<f', struct.pack('<HH', words[a-start], words[a+1-start]))[0]
                        result[f'{label}{i}_' + ('C_float' if label == 'T' else 'ohm')] = value if math.isfinite(value) else None
        if fc == 4 and start <= 30 and start+len(words) > 31:
            result['rtc_utc_seconds'] = words[30-start] | words[31-start] << 16
        return result
    if fc in (1, 2):
        return {'bits_hex': response[3:-2].hex()}
    return {'acknowledged': True}


def register_view(request, response):
    fc = request[1]
    space = {1: 'COILS', 2: 'DI', 3: 'HR', 4: 'IR', 5: 'COILS', 6: 'HR', 16: 'HR'}[fc]
    start = int.from_bytes(request[2:4], 'big')
    if fc in (3, 4):
        values = struct.unpack('>' + 'H' * (response[2] // 2), response[3:-2])
    elif fc in (1, 2):
        count = int.from_bytes(request[4:6], 'big')
        values = [(response[3 + i // 8] >> (i % 8)) & 1 for i in range(count)]
    else:
        return space, {'acknowledged': True}
    width = 1 if fc in (1, 2) else 16
    return space, {f'{start+i:04X}': {'HEX': f'0x{v:04X}', 'BIN': format(v, f'0{width}b'), 'DEC': v}
                   for i, v in enumerate(values)}


class ExchangeLog:
    def __init__(self, directory, session, application):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = Path(directory)/ (datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.log')
        self.write('header', session=copy.deepcopy(session), application=application,
                   configuration_status='Session snapshot; device state is not verified')

    def write(self, event, **fields):
        record = dict(time=datetime.now(timezone.utc).isoformat(), event=event, **fields)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')


def export_log(source, destination):
    rows, columns = [], []
    with Path(source).open(encoding='utf-8-sig') as stream:
        for line in stream:
            item = json.loads(line)
            if item['event'] != 'rx' or not item.get('data') or item.get('function') not in (3, 4):
                continue
            row = dict(pc_utc=item['time'], **item['data'])
            rows.append(row)
            for key in row:
                if key not in columns:
                    columns.append(key)
    with Path(destination).open('w', encoding='utf-8-sig', newline='') as stream:
        import csv
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(columns or ['pc_utc'])
        for row in rows:
            writer.writerow(['NA' if row.get(c) is None else str(row[c]) for c in columns])
    return len(rows)


class Transport:
    def __init__(self, port, timeout, log):
        self.port, self.timeout, self.log = port, timeout, log
        self.last_ascii = ''
        self.last_registers = {}
        self.last_space = ''

    def transact(self, request, send_request=True, stop=None, ignore_functions=()):
        self.last_ascii = ''
        parse_rtu_frame(request.hex())
        wire = ascii_encode(request)
        if send_request:
            self.log.write('tx', protocol='modbus-ascii', ascii=wire.decode('ascii'), hex=wire.hex(' '))
        result = bytearray()
        try:
            if send_request:
                self.port.reset_input_buffer()
                if self.port.write(wire) != len(wire):
                    raise IOError('Неполная отправка')
            end = time.monotonic()+self.timeout
            last_byte = time.monotonic()
            while time.monotonic() < end or (stop is not None and not result):
                if stop is not None and stop.is_set():
                    return None
                chunk = self.port.read(1)
                if result and time.monotonic() - last_byte > 1.0:
                    raise TimeoutError('ASCII: межсимвольный таймаут')
                if not chunk:
                    continue
                last_byte = time.monotonic()
                if chunk == b':':
                    result = bytearray(chunk)
                elif result:
                    result.extend(chunk)
                if len(result) > 513:
                    raise ValueError('ASCII: превышена длина кадра')
                if result.endswith(b'\r\n'):
                    if ignore_functions and ascii_decode(bytes(result))[1] in ignore_functions:
                        result = bytearray()
                        continue
                    break
            received_wire = bytes(result)
            if not result.endswith(b'\r\n'):
                raise TimeoutError('ASCII: таймаут / неполный ответ')
            self.last_ascii = received_wire.decode('ascii', errors='replace')
            result = ascii_decode(received_wire)
            expected = 5 if result[1] & 128 else (result[2]+5 if request[1] in (1,2,3,4) else 8)
            if len(result) != expected:
                raise ValueError('Неверная длина ответа')
            if crc16(result) or result[0] != request[0]:
                raise ValueError('CRC или адрес ответа не совпадает')
            if result[1] == request[1] | 128:
                raise RuntimeError(f'Modbus exception 0x{result[2]:02X}')
            if result[1] != request[1]:
                raise ValueError('Функция ответа не совпадает')
            if request[1] in (1, 2, 3, 4):
                count = int.from_bytes(request[4:6], 'big')
                if result[2] != (count*2 if request[1] in (3, 4) else (count+7)//8):
                    raise ValueError('Количество данных ответа не совпадает')
            elif result[2:6] != request[2:6]:
                raise ValueError('Подтверждение записи не совпадает')
            decoded = decode(request, result)
            self.last_ascii = received_wire.decode('ascii')
            self.last_space, self.last_registers = register_view(request, result)
            self.log.write('rx', protocol='modbus-ascii', ascii=received_wire.decode('ascii'), hex=received_wire.hex(' '), function=request[1], space=self.last_space, registers=self.last_registers, data=decoded)
            return decoded
        except Exception as exc:
            self.log.write('error', message=str(exc), received_hex=(received_wire if 'received_wire' in locals() else bytes(result)).hex(' '))
            raise

    def wait_command(self, address, coil):
        end = time.monotonic()+5
        while time.monotonic() < end:
            reply = self.transact(frame(address, 1, coil, 1))
            if not int(reply['bits_hex'], 16) & 1:
                error = self.transact(frame(address, 4, 29, 1))['001D']
                if error:
                    raise RuntimeError(f'Ошибка выполнения команды: {error}')
                return
            time.sleep(.05)
        raise TimeoutError('Команда не завершилась за 5 секунд')
