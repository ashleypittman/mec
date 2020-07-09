#!/usr/bin/python3

"""Session logging for myenergi devices"""

import sys
import time
import logging

import mec.power_meter

class SessionManager():
    """Session manager"""

    def __init__(self, se):
        self._se = se
        self.session = None
        self._known_charge_added = 0
        self._pm = None

    def update_state(self, state, zappi, have_leaf):
        """Update the session state from the current Zappi state."""

        # Firstly check if we should terminate a session.
        if self.session is not None:
            end_session = False
            if not zappi.car_connected():
                end_session = True
            if zappi.charge_added < self._known_charge_added:
                end_session = True
            if end_session:
                self.session = None
        if self.session is None and zappi.car_connected():
            self.session = self._se.new_session()
            if have_leaf:
                self.session.set_not_leaf()
            self._known_charge_added = zappi.charge_added
            self._pm = mec.power_meter.PowerMeter()

        if not self.session:
            return
        self._pm.add_value(zappi.charge_rate, zappi.time)
        self.session.update(self._pm.kwh())
        self._known_charge_added = zappi.charge_added

    def should_health_charge(self):
        """Returns true if battery low"""
        if not self.session:
            return False
        return self.session.should_health_charge()

    def should_stop_charge(self):
        """Returns true if desireable SOC achieved"""
        if not self.session:
            return False
        return self.session.should_stop_charge()

    def request_update(self):
        if not self.session:
            return
        if hasattr(self.session, 'request_update'):
            self.session.request_update()

class SessionEngine():
    """Metaclass for creating the right kind of session"""

    def __init__(self, conf):
        self._conf = conf
        if 'pycarwings_path' in conf:
            sys.path.append(conf['pycarwings_path'])
        try:
            self._py2 = __import__('pycarwings2')
        except ModuleNotFoundError:
            self._py2 = None

    def new_session(self):
        """Return a new session"""
        if not self._py2:
            return NullSession()
        try:
            return Session(self._conf, self._py2)
        except KeyError:
            return NullSession()

class NullSession():

    """No-op session for when pycarwings2 not available"""
    _is_leaf = None

    def update(self, state):
        """Do nothing"""

    def should_health_charge(self):
        """Does not need health charge"""
        return False

    def should_stop_charge(self):
        """Does not need to stop charge"""
        return False

class Session():

    """Session counter"""

    log = logging.getLogger(__name__)
    # Estimate of battery capacity, in terms of KwH charge to
    # go from 0-100%.  Used for SOC calculations.
    capacity = 26
    # Percentage
    low_capacity = 20
    high_capacity = 80

    def __init__(self, conf, py):
        self.log.debug('Starting new session')
        self._is_leaf = None
        if 'leaf' not in conf:
            return
        leaf_conf = conf['leaf']
        self._start_time = time.gmtime()
        self._base_kwh = None
        self._soc_kwh = None
        self._leaf = None
        self._py = py.Session(leaf_conf['username'], leaf_conf['password'], leaf_conf['region'])
        self._py_import = py
        self._get_leaf()
        self.check_connected = True
        self._refresh = False
        self._refresh_time = None

    def _get_leaf(self):
        if self._leaf:
            return self._leaf
        try:
            self._leaf = self._py.get_leaf()
        except self._py_import.CarwingsError:
            pass
        return self._leaf

    def __del__(self):
        self.log.debug('Closing session')
        if not self._is_leaf:
            return
        if not self._base_kwh and self._soc_kwh:
            return
        if self.check_connected:
            self.log.info('Charge went from %f to %f', self._base_kwh, self._soc_kwh)

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
                self._is_leaf = False
                self.log.info('Leaf is not charging')
                return
            if info.is_connected_to_quick_charger:
                self._is_leaf = False
                self.log.info('Leaf is quick charging, not starting session')
                return
        self._is_leaf = True
        percent = int(info.state_of_charge)
        if not self._base_kwh:
            self._base_kwh = (self.capacity * percent / 100) - kwh
            self._initial_percent = percent
        self.log.info('State of charge is reported as %d%%', percent)
        self.log.info('That is %.1f kWh', self._base_kwh)
        return percent

    def request_update(self):
        """Mark the session as needing an update"""

        self._refresh = True
        self._refresh_time = time.gmtime()

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

    def set_not_leaf(self):
        self.log.debug('Setting car as not Leaf')
        self._is_leaf = False

    def percent_charge(self):
        return (self._soc_kwh / self.capacity) * 100

    def charge_required_for_soc(self, target_soc):
        """Return the kWh required to hit target_soc %"""
        to_add = target_soc - self.percent_charge()
        return self.capacity * (to_add / 100)

    def update(self, kwh):
        """Refresh car data"""
        if self._is_leaf is None:
            self._fetch_latest(kwh, self._start_time)
        if not self._is_leaf:
            return

        self._soc_kwh = self._base_kwh + kwh

        if self._refresh:
            self._do_refresh()

        if self.check_connected:
            self.log.info('Total charge added %.2f', kwh)
            self.log.info('Total charge held %.2f', self._soc_kwh)
            self.log.info('SOC percentage %.0f', self.percent_charge())

    def should_health_charge(self):
        """Returns true if car should charge because of low battery"""

        return self._is_leaf and self._soc_kwh < ((self.capacity * self.low_capacity) / 100)

    def should_stop_charge(self):
        """Returns true if the charge should stop"""

        return self._is_leaf and self._soc_kwh > ((self.capacity * self.high_capacity) / 100)
