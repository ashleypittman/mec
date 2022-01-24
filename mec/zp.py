#!/usr/bin/python3

"""Classes for handling myenergi server data"""

import http
import urllib.request
import datetime
import calendar
import json
import time
import pprint
import logging
import socket

MODES = ['Fault/Startup', 'Fast', 'Eco', 'Eco+', 'Stop']
STATUSES = ['Starting', 'Waiting for export', 'DSR', 'Diverting', 'Boosting', 'Hot']
PSTATUSES = {'A': 'Disconnected',
             'B1': 'Connected',
             'B2': 'Waiting for EV',
             'C1': 'Charge Starting',
             'C2': 'Charging',
             'F': 'Fault'}

# Eddi Boost Types.
EBT = ['Not boostable', 'Boiler', 'Heat Pump', 'Battery']
ESTATUSES = ['?', 'Waiting for surplus', 'Paused', 'Diverting', 'Boost', 'Max Temp Reached', 'Stopped']

E_CODES = {0: 'OK',
           1: 'Invalid ID',
           2: 'Invalid DSR command sequence number',
           3: 'No action taken',
           4: 'Hub not found',
           5: 'Internal Error',
           6: 'Invalid load value',
           7: 'Year missing',
           8: 'Month missing or invalid',
           9: 'Day missing or invalid',
           10: 'Hour missing or invalid',
           11: 'Invalid TTL Value',
           12: 'User not authorised to perform operation',
           13: 'Serial No not found',
           14: 'Missing or bad parameter',
           15: 'Invalid password',
           16: 'New passwords don’t match',
           17: 'Invalid new password',
           18: 'New password is same as old password',
           19: 'User not registered',
           20: 'Minute missing or invalid',
           21: 'Slot missing or invalid',
           22: 'Priority bad or missing',
           23: 'Command not appropriate for device',
           24: 'Check period bad or missing',
           25: 'Min Green Level bad or missing',
           26: 'Busy – Server is already sending a command to the device',
           27: 'Relay not fitted'}

log = logging.getLogger('myenergi')
pp = pprint.PrettyPrinter()

def power_format(watts):
    """Return a string represention of watts"""
    if watts < 1000:
        return '{}w'.format(watts)
    return '{:.3f}kW'.format(watts/1000)

class DataException(Exception):
    """General exception class"""

class DataBogus(DataException):
    """Bogus/invalid data from server"""

class DataTimeout(DataException):
    """Timeout from server"""

class HostChanged(DataException):
    """Server host has changed."""

class ReportCapture:
    """Class for concatenating log strings"""

    def __init__(self):
        self.output = []

    def log(self, line):
        """Add a log line"""
        output = str(line)
        self.output.append(output)
        log.debug(output)

    def get_log(self):
        return '\n'.join(self.output)

    def __str__(self):
        return self.get_log()

class MyEnergiDevice:

    def __init__(self, data, house_data):
        self.sno = self._glimpse(data, 'sno')
        date = self._glimpse(data, 'dat')
        tsam = self._glimpse(data, 'tim')
        self.time = time.strptime('{} {} GMT'.format(date, tsam), '%d-%m-%Y %H:%M:%S %Z')
        elapsed = time.mktime(time.gmtime()) - time.mktime(self.time)
        log.debug('Data from %s is %d second(s) old', type(self), elapsed)
        self._values = {}
        self.data_age = elapsed
        self.firmware = self._glimpse(data, 'fwv')
        if self.sno in house_data and 'name' in house_data[self.sno]:
            self.zname = house_data[self.sno]['name']
        else:
            self.zname = 'Zappi'
        ct = 0
        while True:
            ct += 1
            # These are present in Harvi data for some reason.
            ct_phase = self._glimpse_safe(data, 'ect{}p'.format(ct))
            ct_name_key = 'ectt{}'.format(ct)
            if ct_phase not in {1,0}:
                log.debug('CT %s is on phase %d', ct_name_key, ct_phase)
            if ct_name_key not in data:
                break
            value = self._glimpse_safe(data, 'ectp{}'.format(ct))
            ct_name = self._glimpse(data, ct_name_key)
            if ct_name == 'None':
                continue
            if ct_name == 'Internal Load':
                continue
            if self.sno in house_data and ct_name_key in house_data[self.sno]:
                ct_name = house_data[self.sno][ct_name_key]
                value = value * -1
            if ct_name != 'Grid':
                if ct_name in self._values:
                    self._values[ct_name] += value
                else:
                    self._values[ct_name] = value
            else:
                if not 'Grid' in self._values:
                    self._values['Grid'] = value # only take the first grid value for non-netting 3 phase
                else:
                    if 'net_phases' in house_data:
                        if house_data['net_phases'] == True:
                        # 3 phase all report with same name "grid" so need to sum them
                        # note this produces a net import/export number.
                        # if phases are not netted Zappi assumes export monitoring on phase 1
                            self._values['Grid'] = self._values['Grid'] + value
        log.debug(self._values)

    def _glimpse_safe(self, data, key):
        """Return key and delete from data"""
        if key not in data:
            return 0
        value = data[key]
        del data[key]
        return value

    def _glimpse(self, data, key):
        """Return key and delete from data"""
        value = data[key]
        del data[key]
        return value

    def report(self, rep=None):
        if not rep:
            rep = ReportCapture()

        rep.log(str(self))
        return rep.get_log()

    def get_values(self, key):
        """Return a tuple of (watts, None) for a given device"""

        # This matches Zappi.get_values() but in this case the voltage
        # is not known, so reply None for the amps.
        return (self._values[key], None)

class MyEnergiDiverter(MyEnergiDevice):
    """A Myenergi diverter device"""

    def __init__(self, data, hc):
        super().__init__(data, hc)
        voltage = self._glimpse(data, 'vol')
        if voltage > 1000:
            self.voltage = voltage / 10
        else:
            self.voltage = voltage
        self.frequency = self._glimpse(data, 'frq')
        log.debug('Voltage %f frequency %f', self.voltage, self.frequency)
        self.grid = self._glimpse_safe(data, 'grd')
        self.generation = self._glimpse_safe(data, 'gen')
        self.phase_count = self._glimpse(data, 'pha')
        self.priority = self._glimpse(data, 'pri')

        self.charge_added = self._glimpse_safe(data, 'che')
        self.manual_boost = bool(self._glimpse_safe(data, 'bsm'))
        self.timed_boost = bool(self._glimpse_safe(data, 'bst'))
        self.charge_rate = self._glimpse_safe(data, 'div')

        # Daylight savings and Time Zone.
        self.dst = self._glimpse_safe(data, 'dst')
        self.tz = self._glimpse_safe(data, 'tz')

        self.cmt = self._glimpse_safe(data, 'cmt')
        if self.cmt != 254:
            log.debug('cmt is %d', self.cmt)

class Eddi(MyEnergiDiverter):
    """A Eddi class"""

    def __init__(self, data, hc):
        super().__init__(data, hc)
        # Priority
        self.heater_priority = self._glimpse(data, 'hpri')

        # These appear to be names, but not the same as shown in the app.
        self._glimpse(data, 'ht1')
        self._glimpse(data, 'ht2')

        self.heater_number = self._glimpse(data, 'hno')

        self.status = ESTATUSES[self._glimpse(data, 'sta')]

        # Boost time left, in seconds.
        self.remaining_boost_time = self._glimpse_safe(data, 'rbt')


        self.temp_1 = self._glimpse(data, 'tp1')
        self.temp_2 = self._glimpse(data, 'tp2')

        relay_board = bool(self._glimpse(data, 'rbc'))
        if not relay_board:
            return

        self.relay_1_active = bool(self._glimpse(data, 'r1a'))
        self.relay_2_active = bool(self._glimpse(data, 'r2a'))

        self.relay_1_boost_type = EBT[self._glimpse(data, 'r1b')]
        self.relay_2_boost_type = EBT[self._glimpse(data, 'r2b')]

class Zappi(MyEnergiDiverter):
    """A Zappi class"""

    def __init__(self, data, hc):
        super().__init__(data, hc)
        self.min_green_level = self._glimpse(data, 'mgl')

        self._glimpse_safe(data, 'ectt4')
        self._glimpse_safe(data, 'ectt5')
        self._glimpse_safe(data, 'ectt6')

        self.mode = MODES[self._glimpse_safe(data, 'zmo')]
        self.status = STATUSES[self._glimpse_safe(data, 'sta')]
        self.pstatus = PSTATUSES[self._glimpse(data, 'pst')]
        self._values['Zappi'] = self.charge_rate
        self.manual_boost_level = self._glimpse_safe(data, 'tbk')
        self.smart_boost = bool(self._glimpse_safe(data, 'bss'))
        self.smart_boost_level = self._glimpse_safe(data, 'sbk')
        self.smart_boost_hour = self._glimpse_safe(data, 'sbh')
        self.smart_boost_minute = self._glimpse_safe(data, 'sbm')
        # https://myenergi.info/viewtopic.php?p=19026 for details
        # of locking.
        self.lock = self._glimpse_safe(data, 'lck')
        lock = self.lock
        if lock >= 16:
            # Status
            log.debug('Charge session allowed')
            lock -= 16
        if lock >= 8:
            # Setting
            log.debug('Charge when locked')
            lock -= 8
        if lock >= 4:
            log.debug('Lock when unplugged')
            lock -= 4
        if lock >= 2:
            log.debug('Lock when plugged in')
            lock -= 2
        if lock >= 1:
            log.info('Locked Now')
            lock -= 1

    def boost_active(self):
        """Return True if any kind of boost is active"""
        return self.manual_boost or self.smart_boost or self.timed_boost

    def car_connected(self):
        """Returns True if car is connected"""
        return self.pstatus != 'Disconnected'

    def waiting_for_export(self):
        return self.car_connected() and self.status == 'Waiting for export'

    def min_charge_rate_with_level(self):
        """Return the min charge rate in watts"""
        return int(self.voltage * 6 * self.min_green_level / 100)

    def report(self, rep=None):
        """Return a multi-line test description of the current state"""
        if not rep:
            rep = ReportCapture()
        rep.log(self.zname+' mode is {}'.format(self.mode))
        # The min charge level is often given as 1.4kw however it needs to take into
        # account voltage.
        rep.log('Min Green level is {}% ({})'.format(self.min_green_level,
                                                     power_format(self.min_charge_rate_with_level())))

        rep.log('Car status is {}'.format(self.status))
        (charge_watts, charge_amps) = self.get_values('Zappi')
        if charge_watts:
            rep.log('Car is charging at {} ({:.1f} amps)'.format(power_format(charge_watts), charge_amps))
        if self.charge_added:
            rep.log('Car charge added {}kWh'.format(self.charge_added))
        rep.log('Plug status is {}'.format(self.pstatus))
        if self.manual_boost:
            rep.log('Device is manual boosting')
            rep.log('Manual boost is set to add {}kWh'.format(self.manual_boost_level))
        if self.smart_boost:
            rep.log('Device is smart boosting')
            rep.log('Smart boost is set to add {}kWh by {}:{:02d}'.format(self.smart_boost_level,
                                                                          self.smart_boost_hour,
                                                                          self.smart_boost_minute))

        return rep.get_log()

    def get_values(self, key):
        """Return a tuple of (watts, amps) for a given device"""
        return (self._values[key], self._values[key] / self.voltage)

class Harvi(MyEnergiDevice):
    """A Harvi device"""

# Nothing to do here, disable it.
#    def __init__(self, data):
#        super().__init__(data)

class MyEnergi:
    """Class representing data returned"""

    def __init__(self, raw, check, house_data):
        #
        # Create a new object, takes a data structure returned from json.load()
        #
        log.debug('Data, as received\n%s', pp.pformat(raw))
        self._values = {}
        self._value_time = {}
        self._zid = None
        self._zappis = []
        self._eddis = []
        self._harvis = []
        self._house_data = house_data

        for device in raw:
            for (e, v) in device.items():
                # Skip devices that don't exist.
                if isinstance(v, list) and len(v) == 0:
                    continue
                if e in ('asn', 'fwv'):
                    continue
                for device_data in v:
                    if not isinstance(device_data, dict):
                        continue
                    device_data = dict(device_data)
                    if e == 'zappi':
                        self._zappis.append(Zappi(device_data, house_data))
                    elif e == 'eddi':
                        self._eddis.append(Eddi(device_data, house_data))
                    elif e == 'harvi':
                        self._harvis.append(Harvi(device_data, house_data))
                    if device_data:
                        log.info('Extra data for %s:%s', e, device_data)

        for device in self._zappis + self._eddis + self._harvis:
            for (key, value) in device._values.items():
                if key == 'Zappi':
                    continue
                if key in self._values:
                    self._values[key] += value
                else:
                    self._values[key] = value
                self._value_time[key] = device.time
        if check:
            for device in self._zappis + self._eddis:
                if device.voltage == 0:
                    raise DataBogus
                self._check_device_value(device.generation, 'Generation')
                self._check_device_value(device.grid, 'Grid')

    def zappi_list(self, priority_order=False):
        # Return a constant-order Zappi list.

        if priority_order:
            return sorted(self._zappis, key=lambda d: d.priority)
        else:
            return sorted(self._zappis, key=lambda d: d.sno)

    def eddi_list(self, priority_order=False):
        # Return a constant-order Eddi list.

        if priority_order:
            return sorted(self._eddis, key=lambda d: d.priority)
        else:
            return sorted(self._eddis, key=lambda d: d.sno)

    def _check_device_value(self, val, vname):

        for harvi in self._harvis:
            if harvi.data_age > 120:
                log.warning('Harvi data is old')
                return
        try:
            val2 = self._values[vname]
        except KeyError:
            return
        if val != val2:
            self._values[vname] = int((val + val2)/2)
            diff = abs(val - val2)
            if diff > 200:

                try:
                    percent = diff/abs(self._values[vname]/100)
                except ZeroDivisionError:
                    # This has happened when the CT is reading 1940
                    # one Zappi is reporting 1940 and one is reporting
                    # -1939
                    percent = 6
                if percent > 5:
                    log.info("Discrepancy in %s values: %d %d", vname, val, val2)
                    log.info("{:.2f}% difference".format(percent))
                    raise DataBogus

    def get_readings(self):
        """Generator function for returning power values"""
        for key in self._values:
            yield(key, self._values[key], self._value_time[key])

    def report(self, sockets):
        """Return a string describing current states"""

        rep = ReportCapture()

        house_use = self._values['Grid']

        if 'Generation' in self._values:
            house_use += self._values['Generation']

        try:
            house_use -= self._values['iBoost']
            house_use -= self._values['Heating']
            rep.log('Heating is using {}'.format(self._values['Heating']))
        except KeyError:
            pass

        for zappi in self.zappi_list():
            zappi.report(rep)
            house_use -= zappi.charge_rate

        sockets_total = 0
        kwh_today = 0
        if sockets:
            for device in sockets:
                rep.log(device)
                house_use -= device.watts
                if device.on and device.mode in ['auto']:
                    sockets_total += device.watts
                kwh_today += device.todays_kwh()
        if kwh_today:
            rep.log('Total used by sockets today {:.2f}kWh'.format(kwh_today))
        self._values['House'] = house_use
        # This one isn't strictly correct as it's computed from different inputs
        # which may have different sample times.
        self._value_time['House'] = self._value_time['Grid']
        rep.log('House is using {}'.format(power_format(house_use)))
        if sockets_total:
            rep.log('Sockets are using {}'.format(power_format(sockets_total)))
        #(iboost_watts, iboost_amps) = self._values('iBoost')
        #rep.log('iBoost is using {} ({:.1f} amps)'.format(power_format(iboost_watts), iboost_amps))
        if 'iBoost' in self._values:
            iboost_watts = self._values['iBoost']
            rep.log('iBoost is using {}'.format(power_format(iboost_watts)))
        if 'Generation' in self._values:
            rep.log('Solar is generating {}'.format(power_format(self._values['Generation'])))
        grid = self._values['Grid']
        if grid > 0:
            rep.log('Importing {}'.format(power_format(grid)))
        else:
            rep.log('Exporting {}'.format(power_format(-grid)))

        return str(rep)

ASN='X_MYENERGI-asn'

class MyEnergiHost:
    """Class for downloading data"""

    def __init__(self, username, password, house_conf={}):
        self.__username = str(username)
        self.__password = password
        #self.__host = 'director.myenergi.net'
        self.__host = 's18.myenergi.net'
        self.state = None
        self._house_conf = house_conf

    def _maybe_set_host(self, headers):
        # Check the returned headers to check if a different host
        # should be used, see
        # https://myenergi.info/update-to-active-server-redirects-t2980.html


        if ASN not in headers:
            return
        if headers[ASN] == self.__host:
            return
        if headers[ASN] == 'undefined':
            return
        log.debug('Changing host to {}'.format(headers[ASN]))
        self.__host = headers[ASN]
        raise HostChanged

    def _load(self, suffix='cgi-jstatus-*'):
        # Connect to myenergi servers, retrying with new host up to
        # three times.
        for _ in range(2):
            try:
                return self._do_load(suffix)
            except HostChanged:
                pass
        # Finally, just try it again, but don't catch it this time.
        return self._do_load(suffix)

    def _do_load(self, suffix):
        # Connect to the myenergi servers and return
        # python dict of results.

        url = 'https://{}/{}'.format(self.__host, suffix)
        start_time = time.time()

        req = urllib.request.Request(url)

        req.add_header('User-Agent', 'Wget/1.14 (linux-gnu)')

        realm = 'MyEnergi Telemetry'

        auth_handler = urllib.request.HTTPPasswordMgr()
        try:
            auth_handler.add_password(user=self.__username,
                                      uri=url,
                                      realm=realm,
                                      passwd=self.__password)
        except ConnectionResetError:
            raise DataTimeout

        handler = urllib.request.HTTPDigestAuthHandler(auth_handler)
        opener = urllib.request.build_opener(handler)
        urllib.request.install_opener(opener)

        try:
            stream = urllib.request.urlopen(req, timeout=20)
            log.debug('Response was %s', stream.getcode())
            self._maybe_set_host(stream.headers)
        except urllib.error.HTTPError as stream:
            self._maybe_set_host(stream.headers)
            raise DataTimeout
        except urllib.error.URLError:
            # Timeout from server.
            raise DataTimeout
        except socket.timeout:
            raise DataTimeout
        except http.client.RemoteDisconnected:
            raise DataTimeout
        except ConnectionResetError:
            raise DataTimeout
        try:
            raw_data = stream.read()
            duration = time.time() - start_time
            log.debug('Load took %.1f seconds', duration)
            data = json.loads(raw_data)
            if 'status' in data:
                status = int(data['status'])
                data['status'] = status
                if -status in E_CODES and data['statustext'] == '':
                    data['statustext'] = E_CODES[-status]
                    log.debug('request failed %s', suffix)
                    log.debug('Error code is %s', E_CODES[-status])
            return data
        except socket.timeout:
            raise DataTimeout

    def refresh(self, check=False):
        """Fetch most recent data."""
        self.state = MyEnergi(self._load(), check, self._house_conf)

    def report_latest(self, sockets):
        """Display most recent data."""
        print(self.state.report(sockets))

    def __set_mode(self, mode, zid):
        log.debug('Setting mode to %s', mode)
        try:
            data = self._load(suffix='cgi-zappi-mode-Z{}-{}-0'.format(zid, mode))
            log.debug(data)
            return data
        except DataException as e:
            log.debug('Error setting mode')
            log.debug(e)
            return 'Exception'

    def set_mode_stop(self, zid):
        """Set mode to stop"""
        return self.__set_mode(4, zid)

    def set_mode_fast(self, zid):
        """Set mode to fast"""
        return self.__set_mode(1, zid)

    def set_mode_eco(self, zid):
        """Set mode to eco"""
        return self.__set_mode(2, zid)

    def set_mode_ecop(self, zid):
        """Set mode to eco plus"""
        return self.__set_mode(3, zid)

    def set_green_level(self, level, zid):
        """Set min green level"""
        res = self._load(suffix='cgi-set-min-green-Z{}-{}'.format(zid, level))
        log.debug(res)

    def _sno_to_key(self, sno):
        """Return the API key for sno"""
        target = None

        for dev in self.state.eddi_list():
            if dev.sno == sno:
                return 'E{}'.format(sno)
        for dev in self.state.zappi_list():
            if dev.sno == sno:
                return 'Z{}'.format(sno)

        raise Exception('serial number not found')

    def get_boost(self, sno):
        """Display active boost settings"""

        key = self._sno_to_key(sno)

        res = self._load(suffix='cgi-boost-time-{}'.format(key))
        log.debug(res)
        self._show_timed_boost(res)

    def _show_timed_boost(self, res, slot=None):
        times = res['boost_times']
        for instance in times:
            if slot and slot != instance['slt']:
                continue

            boost_days = []
            # day_mask is a bit odd, it's a sequence of eight
            # 0/1 values, the 1st is always 0, the rest represent
            # days of the week.
            day_mask = instance['bdd']
            for dow, val in enumerate(day_mask[1:]):
                if val == '1':
                    boost_days.append(calendar.day_name[dow])

            if boost_days:
                # Convert from start time + duration to start time + end time.
                start_time = datetime.datetime(year=1977, month=1, day=1,
                                               hour=instance['bsh'],
                                               minute=instance['bsm'])
                duration = datetime.timedelta(hours=instance['bdh'],
                                              minutes=instance['bdm'])
                end_time = start_time + duration
                if duration.seconds != 0:
                     print('Start {} End {} (duration {:02d}:{:02d}) days {}'.format(
                        start_time.strftime('%H:%M'), end_time.strftime('%H:%M'),
                        instance['bdh'], instance['bdm'], ','.join(boost_days)))
            del instance['bsh']
            del instance['bsm']
            del instance['bdh']
            del instance['bdm']
            del instance['bdd']
            del instance['slt']
            if instance:
                print(instance)
        del res['boost_times']
        if res:
            print(res)

    def set_boost(self, zid, slot, bsh=0, bsm=0, bdh=0, bdm=0, bdd=None, dow=None):

        # cgi-boost-time-Z???-{slot}-{bsh}-{bdh}-{bdd}
        # Slot is one of 11,12,13,14
        # Start time is in 24 hour clock, 15 minute intervals.
        # Duration is hoursminutes and is less than 10 hours.
        if dow is not None:
            bdd = list('00000000')
            bdd[dow+1] = '1'
            bdd = ''.join(bdd)
        elif not bdd:
            bdd = '00000000'
        if (bdh >= 8):
            log.info('Max 8 hours per slot')
            bdh = 8
            bdm = 0

        res = self._load(suffix='cgi-boost-time-Z{}-{}-{:02}{:02}-{}{:02}-{}'.format(zid,
                                                                                     slot,
                                                                                     bsh,
                                                                                     bsm,
                                                                                     bdh,
                                                                                     bdm,
                                                                                     bdd))
        if 'status' in res and res['status'] != 0:
            log.info('Error code is %s', E_CODES[-res['status']])
            return
        self._show_timed_boost(res, slot=slot)

    def stop_boost(self, zid):

        res = self._load(suffix='cgi-zappi-mode-Z{}-0-2-0-0'.format(zid))
        print(res)

    def get_hour_data(self, zid, day=None):
        """Return hourly data for today"""
        if not day:
            day = time.localtime()

        res = self._load(suffix='cgi-jdayhour-Z{}-{}-{}-{}'.format(zid,
                                                                   day.tm_year,
                                                                   day.tm_mon,
                                                                   day.tm_mday))
        key = 'U{}'.format(zid)
        if key in res:
            return res[key]
        return res

    def get_minute_data(self, zid, day=None):
        """Return minute data for today"""
        if not day:
            day = time.localtime()

        sh = 0
        sm = 0
        mc = 1440
#        mc = 50

        if True:
            res = self._load(suffix='cgi-jday-Z{}-{}-{}-{}-{}-{}-{}'.format(zid,
                                                                   day.tm_year,
                                                                   day.tm_mon,
                                                                   day.tm_mday,
                                                                   sh,
                                                                   sm,
                                                                   mc))
        else:
            res = self._load(suffix='cgi-jday-Z{}-{}-{}-{}'.format(zid,
                                                                   day.tm_year,
                                                                   day.tm_mon,
                                                                   day.tm_mday))
        key = 'U{}'.format(zid)
        if key in res:
            return res[key]
        return res

    def set_heater_priority(self, heater, eid):
        if heater:
            res = self._load(suffix='cgi-set-heater-priority-E{}'.format(eid))
            cpm = res['cpm']
            res = self._load(suffix='cgi-set-heater-priority-E{}-{}-{}'.format(eid, heater, cpm))
        else:
            res = self._load(suffix='cgi-set-heater-priority-E{}'.format(eid))
        log.debug(res)
        return res['hpri']
