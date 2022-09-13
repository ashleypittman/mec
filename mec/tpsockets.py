#!/usr/bin/env python3

from struct import pack
import socket
import calendar
import logging
import time
import json

import mec.power_meter

# Encryption and Decryption of TP-Link Smart Home Protocol
# XOR Autokey Cipher with starting key = 171


def encrypt(string):
    """Encrypt data to send to sockets"""
    key = 171
    result = pack('>I', len(string))
    for i in string:
        a = key ^ ord(i)
        key = a
        result += b'%c' % a
    return result


def decrypt(string):
    """Decrypt data from sockets"""
    key = 171
    result = ""
    for i in string:
        a = key ^ i
        key = i
        result += chr(a)
    return result


class History():

    """Class for recording state/energy history of sockets"""

    def __init__(self):
        # If the socket is currently on
        self.is_on = None
        # The timestamp when the last change happened.
        self._last_change = None
        #
        self._last_record = None
        # The runtime in seconds since Object creation
        self._runtime = 0
        self.power_states = []

    def set_entry(self, stime, is_on, power=None):
        """Make an entry in the history"""
        if self.is_on is None:
            self.is_on = is_on
            self._last_change = time.mktime(stime)
            self._last_record = self._last_change
            return

        sample_time = time.mktime(stime)
        elapsed = int(sample_time - self._last_record)
        self._last_record = sample_time

        if is_on != self.is_on:
            self._last_change = sample_time
            self.is_on = is_on

        if not self.is_on:
            return

        self._runtime += elapsed

        if power is None:
            return

        if not self.power_states:
            self.power_states.append((power, self._runtime))
            return

        for index, (rec_power, _) in enumerate(self.power_states):
            if power < rec_power:
                continue
            if power >= rec_power:
                self.power_states[index] = (power, self._runtime)
            del self.power_states[index+1:]

        # If power is less than the last record then add a new entry.
        (rec_power, rec_ts) = self.power_states[-1]
        if power < rec_power and self._runtime != rec_ts:
            self.power_states.append((power, self._runtime))

    def get_max_power(self):
        """Return the maximum amount of power used recently."""
        if not self.power_states:
            return 0
        (power, _) = self.power_states[0]
        return power

    def is_satisfied(self, power=25, runtime=600):
        """Return true if the device is consuming little power"""

        if not self.power_states:
            return False

        # Now walk the list, looking for entries where the device
        # is using less than 25 watts for the last 10 minutes of
        # runtime.
        for (rec_power, rec_ts) in self.power_states:
            if self._runtime - rec_ts > runtime:
                continue
            if rec_power > power:
                return False
            return True

        return False


class PowerSocketConnection():
    """Class for working with TPLINK sockets"""

    log = logging.getLogger(__name__)

    def __init__(self, hostname):
        self._host = hostname
        self.name = None
        self.on = None
        self.watts = 0
        self.have_energy = False
        self.mode = None
        self._initial_strike_count = 2
        self._strike_count = self._initial_strike_count
        self._initial_power = 0
        self._power = 0
        self._history = History()
        self.external_change = False
        self.ec_time = 0
        self.on_time = None
        self.duration = 2
        self.pm = mec.power_meter.PowerMeter()

    def set_initial_power(self, power):
        """Set expected power usage"""
        self._initial_power = power

    def _set_power(self, power):
        if power > self._power:
            self._power = power

    def get_power(self):
        """Get the maximum power used by the socket"""
        if self._power:
            return self._power
        return self._initial_power

    def strike(self):
        """Make a strike against the socket"""
        # Call this method several times in a row to turn
        # the socket off.
        self._strike_count -= 1
        self.log.info("Strike count for '%s' is %d",
                      self.name, self._strike_count)
        if self._strike_count == 0:
            self.turn_off()
            self.reset_strike_count()

    def reset_strike_count(self):
        """Reset the strike count"""
        self._strike_count = self._initial_strike_count

    def reset_day(self):
        """Reset power for day"""
        self.pm.reset_value()

    def __str__(self):
        if self.on:
            state = 'On'
        else:
            state = 'Off'
        if self.have_energy:
            return "Device '{}' {} {} Watts " \
                   "({:0.2f}kWh today)".format(self.name, state, self.watts,
                                               self.pm.kwh())
        return "Device '{}' {}".format(self.name, state)

    def __repr__(self):
        return "{}'{}'".format(self.__class__, self.name)

    def todays_kwh(self):
        """Return kwh used today"""
        if not self.have_energy:
            return 0
        return self.pm.kwh()

    def _send_cmd(self, major, minor, key=None, value=None, k2=None, v2=None):

        data = {}
        if key:
            data[key] = value
            if k2:
                data[k2] = v2
        d2 = {}
        d2[major] = {}
        d2[major][minor] = data
        cmd = json.dumps(d2, separators=(',', ':'))
        try:
            sock_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock_tcp.settimeout(2)
            sock_tcp.connect((self._host, 9999))
            sock_tcp.send(encrypt(cmd))
            data = sock_tcp.recv(4096)
            data2 = b''
            raw = decrypt(data[4:])
            if raw[-1] != '}':
                data2 = sock_tcp.recv(4096)
                raw = decrypt(data[4:] + data2)
            try:
                j = json.loads(raw)
            except json.decoder.JSONDecodeError:
                d3 = sock_tcp.recv(4096)
                raw = decrypt(data[4:] + data2 + d3)
                j = json.loads(raw)

            sock_tcp.close()

            res = j[major][minor]
            if res['err_code'] != 0:
                self.log.debug(res['err_msg'])
                return None
            return res

        except socket.error:
            self.log.info('Cound not connect to host %s:9999 (%s)',
                          self._host, self.name)
            return None
        except json.decoder.JSONDecodeError:
            self.log.info('Error deconding json')
            return None

    def turn_off(self):
        """Turn off socket"""
        self.log.debug("Turning off '%s'", self.name)
        self.external_change = False
        res = self._send_cmd('system', 'set_relay_state', 'state', 0)
        if res:
            self.on = False
            self.log.info("Turned off '%s'", self.name)

    def turn_on(self):
        """Turn on socket"""
        self.log.debug("Turning on '%s'", self.name)
        self.external_change = False
        res = self._send_cmd('system', 'set_relay_state', 'state', 1)
        if res:
            self.on = True
            self.log.info("Turned on '%s'", self.name)

    def get_data(self):
        """Read data from socket"""

        stime = time.gmtime()
        res = self._send_cmd('system', 'get_sysinfo')
        if not res:
            return

#        print(res['rssi'])
        self.name = res['alias']
        self.pm.name = self.name
        on = bool(res['relay_state'])
        if self.on is not None and on != self.on:
            self.log.debug('Socket %s state changed externally from %s to %s',
                           self.name, self.on, on)
            self.external_change = True
            self.ec_time = time.time()
        self.on = on

        if res['feature'] != 'TIM:ENE':
            self._history.set_entry(stime, on)
            return

        self.have_energy = True

        res = self._send_cmd('emeter', 'get_realtime')
        if not res:
            return

        if 'current' in res:
            current = res['current']
            voltage = res['voltage']
        else:
            current = float(res['current_ma']) / 1000
            voltage = float(res['voltage_mv']) / 1000

        self.watts = int(voltage * current)
        self.pm.add_value(self.watts, stime)
        self._history.set_entry(stime, on, self.watts)
        if self._history.is_satisfied(runtime=10):
            if self._history.is_satisfied():
                # No power for 10 minutes.
                self.log.debug('Device %s is happy', self.name)
            else:
                # No power for 10 seconds.
                self.log.debug('Device %s is idle', self.name)
        if self.on:
            self._set_power(self.watts)

    def load_todays_power(self):
        """Calculate the power used today"""
        self.get_data()
        if not self.have_energy:
            return
        today = time.localtime()
        res = self._send_cmd('emeter', 'get_daystat', 'year', today.tm_year,
                             'month', today.tm_mon)
        if not res:
            return
        for day in res['day_list']:
            if day['day'] != today.tm_mday:
                continue
            if 'energy' in day:
                energy = day['energy'] * 1000
            else:
                energy = day['energy_wh']
        self.log.debug('Setting initial energy to {}'.format(energy/1000))
        self.pm.reset_value(kwh=energy/1000)

    def read_igain(self):

        today = time.localtime()
        res = self._send_cmd('emeter', 'get_monthstat', 'year', today.tm_year)
        if not res:
            return
        for month in res['month_list']:
            if 'energy' in month:
                energy = month['energy'] * 1000
            else:
                energy = month['energy_wh']
            print('{} {} {}kwh'.format(calendar.month_abbr[month['month']],
                                       month['year'],
                                       energy/1000))

        res = self._send_cmd('emeter', 'get_daystat', 'year', today.tm_year,
                             'month', today.tm_mon)
        if not res:
            return
        for day in res['day_list']:
            if 'energy' in day:
                energy = day['energy'] * 1000
            else:
                energy = day['energy_wh']
            if not energy:
                continue
            print('{} {} {} {}kwh'.format(calendar.month_abbr[day['month']],
                                          day['day'],
                                          day['year'],
                                          energy/1000))
