#!/usr/bin/python3

"""Session logging for myenergi devices"""

import sys
import time
import math
import logging

import mec.power_meter


class SessionManager():
    """Session manager"""

    def __init__(self, se):
        self._se = se
        self.session = None
        self._known_charge_added = 0
        self._pm = None
        self._have_stopped = False
        self._requested_stop = False

    def update_state(self, state, zappi, have_car):
        """Update the session state from the current Zappi state."""

        # Firstly check if we should terminate a session.
        if self.session is not None:
            end_session = False
            if not zappi.car_connected():
                end_session = True
            if zappi.charge_added < self._known_charge_added:
                # On the new server software the che value
                # can fluctuate, so for now only reset this
                # if the charge has dropped to a very low
                # figure.
                if zappi.charge_added < 1:
                    end_session = True
            if end_session:
                self.session = None
        if self.session is None and zappi.car_connected():
            self.session = self._se.new_session(have_car=have_car)
            self._known_charge_added = zappi.charge_added
            self._pm = mec.power_meter.PowerMeter()

        if not self.session:
            return
        self._pm.add_value(zappi.charge_rate, zappi.time)
        self.session.update(self._pm.kwh())
        self._known_charge_added = zappi.charge_added
        if self._requested_stop and zappi.mode == 'Stop':
            self._have_stopped = True
            self._requested_stop = False

    def should_health_charge(self):
        """Returns true if battery low"""
        if not self.session:
            return False
        return self.session.should_health_charge()

    def should_stop_charge(self):
        """Returns true if desireable SOC achieved"""
        if not self.session:
            return False
        if self._have_stopped:
            return False
        if self._requested_stop:
            return True
        self._requested_stop = self.session.should_stop_charge()
        return self._requested_stop

    def request_update(self):
        if not self.session:
            return
        if hasattr(self.session, 'request_update'):
            self.session.request_update()


class SessionEngine():
    """Metaclass for creating the right kind of session"""

    def __init__(self, conf):
        self._conf = conf
        self._py2 = None
        self._mt = None
        self._jlr = None
        if 'leaf' in conf:

            if 'pycarwings_path' in conf:
                sys.path.append(conf['pycarwings_path'])
            try:
                self._py2 = __import__('pycarwings2')
            except ModuleNotFoundError:
                self._py2 = None
        if 'tesla' in conf:
            self._mt = __import__('myTesla')

        if 'jlr' in conf:
            self._jlr = __import__('jlrpy')

    def new_session(self, have_car=False):
        """Return a new session"""
        if have_car:
            return NullSession()
        if self._py2:
            try:
                return LeafSession(self._conf, self._py2)
            except KeyError:
                return NullSession()
        elif self._mt:
            return TeslaSession(self._conf, self._mt)
        elif self._jlr:
            return jlrSession(self._conf, self._jlr)
        else:
            return NullSession()


class NullSession():

    """No-op session for when pycarwings2 not available"""
    _is_valid = False

    def update(self, state):
        """Do nothing"""

    def should_health_charge(self):
        """Does not need health charge"""
        return False

    def should_stop_charge(self):
        """Does not need to stop charge"""
        return False


class CommonSession():

    log = logging.getLogger(__name__)
    capacity = 26
    low_capacity = 20
    high_capacity = 80
    charge_rate = None

    def __init__(self, conf):
        self.log.debug('Starting new session')
        if 'capacity' in conf:
            self.capacity = conf['capacity']
        if 'charge_below' in conf:
            self.low_capacity = conf['charge_below']
        if 'stop_at' in conf:
            self.high_capacity = conf['stop_at']
        print(self.high_capacity)
        self.check_connected = True
        self._soc_kwh = None
        self._refresh = False
        self._refresh_time = None
        self._is_valid = None
        super().__init__()

    def __del__(self):
        self.log.debug('Closing session')

        if not self._soc_kwh:
            return
        if self.check_connected:
            self.log.info('Charge went from %f to %f', self._base_kwh, self._soc_kwh)

    def request_update(self):
        """Mark the session as needing an update"""

        self._refresh = True
        self._refresh_time = time.gmtime()

    def percent_charge(self):
        return (self._soc_kwh / self.capacity) * 100

    def charge_required_for_soc(self, target_soc):
        """Return the kWh required to hit target_soc %"""
        to_add = target_soc - self.percent_charge()
        return self.capacity * (to_add / 100)

    def should_health_charge(self):
        """Returns true if car should charge because of low battery"""

        if not self.low_capacity:
            return False

        return self._soc_kwh and self._soc_kwh < ((self.capacity * self.low_capacity) / 100)

    def should_stop_charge(self):
        """Returns true if the charge should stop"""

        if not self.high_capacity:
            return False

        return self._soc_kwh and self._soc_kwh > ((self.capacity * self.high_capacity) / 100)


class TeslaSession(CommonSession):

    capacity = 70
    charge_rate = 7200
    name = 'Tesla'

    def __init__(self, conf, mt):
        super().__init__(conf['tesla'])
        tesla_conf = conf['tesla']
        try:
            self._mt = mt.connect(tesla_conf['username'], tesla_conf['password'])
            self._mt.wake_up()
        except KeyError:
            self._mt = None
            self._is_valid = False
        self._base_kwh = None

    def _get_soc(self):
        if not self._mt:
            return
        raw = self._mt.charge_state()
        self.log.debug(raw)
        if 'error' in raw:
            return
        self._is_valid = True
        return raw['response']['battery_level']

    def _do_refresh(self):

        new_percent = self._get_soc()

        self.log.info('State of charge update %d%% %d%%',
                      self.percent_charge(),
                      new_percent)
        # Calculate the observed capacity
        added_percent = new_percent - self._initial_percent
        added_kwh = self._soc_kwh - self._base_kwh
        self.log.info('Percent %d %d %d', added_percent, new_percent, self._initial_percent)

        if added_percent == 0:
            return

        new_cap = added_kwh * (100 / added_percent)
        self.log.info('Capacity change from %.1f to %.1f after %.1f kwh',
                      self.capacity,
                      new_cap,
                      added_kwh)

    def update(self, kwh):

        if self._is_valid is False:
            return

        if not self._base_kwh:
            percent = self._get_soc()
            if not percent:
                return
            self._initial_percent = percent
            self._base_kwh = (self.capacity * self._initial_percent / 100) - kwh

        self._soc_kwh = self._base_kwh + kwh

        if self._refresh:
            self._do_refresh()

        if self.check_connected:
            self.log.info('Total charge added %.2f', kwh)
            self.log.info('Total charge held %.2f', self._soc_kwh)
            self.log.info('SOC percentage %.0f', self.percent_charge())


class LeafSession(CommonSession):
    """Session counter"""

    # Estimate of battery capacity, in terms of KwH charge to
    # go from 0-100%.  Used for SOC calculations.
    capacity = 26
    charge_rate = 6600
    name = 'Leaf'

    def __init__(self, conf, py):
        super().__init__(conf['leaf'])
        leaf_conf = conf['leaf']
        self._start_time = time.gmtime()
        self._base_kwh = None
        self._leaf = None
        self._py = py.Session(leaf_conf['username'], leaf_conf['password'], leaf_conf['region'])
        self._py_import = py
        self._get_leaf()

    def _get_leaf(self):
        if self._leaf:
            return self._leaf
        try:
            self._leaf = self._py.get_leaf()
        except self._py_import.CarwingsError:
            pass
        except KeyError:
            pass
        return self._leaf

    def _fetch_latest(self, kwh, start_time):
        leaf = self._get_leaf()
        if not leaf:
            return
        try:
            info = leaf.get_latest_battery_status()
        except TypeError:
            self.log.exception('Caught TypeError')
            return
        except ValueError:
            self.log.exception('Caught ValueError')
            return
        except KeyError:
            self.log.exception('Caught KeyError')
            return
        except self._py_import.CarwingsError:
            self.log.exception('Caught CarwingsError')
            return

        if not info:
            return
        remote_time = info.answer['BatteryStatusRecords']['NotificationDateAndTime']
        server_time = time.strptime('{} GMT'.format(remote_time), '%Y/%m/%d %H:%M %Z')
        age = time.mktime(start_time) - time.mktime(server_time)
        if info.is_connected and not info.is_connected_to_quick_charger:
            age -= 60
        if not self.check_connected:
            # If the session is not checking the car is charging then it's
            # just being used to check SOC to know how much to add, so
            # accept any value that's less than ten minutes old.
            age -= (60*10)
        if age > 0:
            self.log.info('Data is %d seconds too old', age)
            return

        if self.check_connected:
            if not info.is_connected:
                self._is_valid = False
                self.log.info('Leaf is not charging')
                return
            if info.is_connected_to_quick_charger:
                self._is_valid = True
                self.log.info('Leaf is quick charging, not starting session')
                return
        self._is_valid = True
        percent = int(info.state_of_charge)
        if not self._base_kwh:
            self._base_kwh = (self.capacity * percent / 100) - kwh
            self._initial_percent = percent
        self.log.info('State of charge is reported as %d%%', percent)
        self.log.info('That is %.1f kWh', self._base_kwh)
        return percent

    def _do_refresh(self):
        """Perform a refresh against Nissan servers"""

        new_percent = self._fetch_latest(0, self._refresh_time)
        if not new_percent:
            return

        self._refresh = False

        self.log.info('State of charge update %d%% %d%%',
                      self.percent_charge(),
                      new_percent)
        # Calculate the observed capacity
        added_percent = new_percent - self._initial_percent
        added_kwh = self._soc_kwh - self._base_kwh
        self.log.info('Percent %d %d %d', added_percent, new_percent, self._initial_percent)

        if added_percent == 0:
            return

        new_cap = added_kwh * (100 / added_percent)
        self.log.info('Capacity change from %.1f to %.1f after %.1f kwh',
                      self.capacity,
                      new_cap,
                      added_kwh)

    def update(self, kwh):
        """Refresh car data"""
        if self._is_valid is None:
            self._fetch_latest(kwh, self._start_time)
        if not self._is_valid:
            return

        self._soc_kwh = self._base_kwh + kwh

        if self._refresh:
            self._do_refresh()

        if self.check_connected:
            self.log.info('Total charge added %.2f', kwh)
            self.log.info('Total charge held %.2f', self._soc_kwh)
            self.log.info('SOC percentage %.0f', self.percent_charge())


class jlrSession(CommonSession):
    """Session counter"""

    # Estimate of battery capacity, in terms of KwH charge to
    # go from 0-100%.  Used for SOC calculations.
    capacity = 80
    charge_rate = 7400
    home_latitude = 0.0
    home_longitude = 0.0
    name = 'Jaguar'

    def __init__(self, conf, jlr):
        super().__init__(conf['jlr'])
        jlr_conf = conf['jlr']
        self._start_time = time.gmtime()
        self._base_kwh = None
        self.capacity = jlr_conf['capacity']
        self.charge_rate = jlr_conf['charge_rate']
        self.home_latitude = jlr_conf['home_latitude']
        self.home_longitude = jlr_conf['home_longitude']
        self._jlr = jlr.Connection(jlr_conf['username'], jlr_conf['password']).vehicles[0]

    def _get_soc(self):
        if not self._jlr:
            return
        try:
            current_soc = int(self._jlr.get_status('EV_STATE_OF_CHARGE'))
        except:
            self.log.info('cant connect to car right now')
            # authentication can fail if you request too often
            return
        self._is_valid = True
        return current_soc

    def _fetch_latest(self, kwh, start_time):
        at_home = True
        if (self.home_latitude != 0.0 and self.home_longitude != 0.0):
            p = self._jlr.get_position()
            position = (p['position']['latitude'], p['position']['longitude'])
            home = (float(self.home_latitude), float(self.home_longitude))
            d = int(1000 * distance(home, position))
            if (d > 100):
                self.log.info("car is not at home : "+str(d)+"m from normal location")
                at_home = False
        try:
            vehicleStatus = self._jlr.get_status()['vehicleStatus']
        except:
            self.log.info('cant connect to car right now')
            return
        status = {d['key']: d['value'] for d in vehicleStatus['evStatus']}
        charging_status = status('EV_CHARGING_STATUS')
        current_soc = status('EV_STATE_OF_CHARGE')
        self.log.info("current SoC is "+str(current_soc)+"%")

        if (status('EV_CHARGING_METHOD') == "WIRED" and at_home):
            self.log.info("car is at home and charging status is "+str(charging_status))
        else:
            self.log.info("car is not plugged in or not at home")
            return
        return current_soc

    def _do_refresh(self):

        new_percent = self._fetch_latest(0, self._refresh_time)
        if not new_percent:
            return

        self._refresh = False

        self.log.info('State of charge update %d%% %d%%',
                      self.percent_charge(),
                      new_percent)
        # Calculate the observed capacity
        added_percent = new_percent - self._initial_percent
        added_kwh = self._soc_kwh - self._base_kwh
        self.log.info('Percent %d %d %d', added_percent, new_percent, self._initial_percent)

        if added_percent == 0:
            return

        new_cap = added_kwh * (100 / added_percent)
        self.log.info('Capacity change from %.1f to %.1f after %.1f kwh',
                      self.capacity,
                      new_cap,
                      added_kwh)

    def update(self, kwh):

        if self._is_valid is False:
            return

        if not self._base_kwh:
            percent = self._get_soc()
            if not percent:
                return
            self._initial_percent = percent
            self._base_kwh = (self.capacity * self._initial_percent / 100) - kwh

        self._soc_kwh = self._base_kwh + kwh

        if self._refresh:
            self._do_refresh()

        if self.check_connected:
            self.log.info('Total charge added %.2f', kwh)
            self.log.info('Total charge held %.2f', self._soc_kwh)
            self.log.info('SOC percentage %.0f', self.percent_charge())

    def distance(origin, destination):
        """
        From https://stackoverflow.com/questions/19412462
        Calculate the Haversine distance.
        Parameters
        ----------
        origin : tuple of float
            (lat, long)
        destination : tuple of float
            (lat, long)
        Returns
            -------
        distance_in_km : float
        Examples
        --------
        >>> origin = (48.1372, 11.5756)  # Munich
        >>> destination = (52.5186, 13.4083)  # Berlin
        >>> round(distance(origin, destination), 1)
        504.2
        """
        lat1, lon1 = origin
        lat2, lon2 = destination
        radius = 6371  # km

        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) * math.sin(dlat / 2) +
            math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
            math.sin(dlon / 2) * math.sin(dlon / 2))
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        d = radius * c
        return d
