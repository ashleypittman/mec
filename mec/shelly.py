import logging
import urllib.request
import json
import time

import mec.power_meter

class PowerSocketConnection():
    """Class for working with TPLINK sockets"""

    log = logging.getLogger(__name__)

    def __init__(self, hostname):
        self._host = hostname
        self.mode = None
        self.pm = mec.power_meter.PowerMeter()
        self.name = 'shelly'
        self._initial_strike_count = 2
        self._strike_count = self._initial_strike_count
        self.on_time = None
        self.external_change = False
        self.ec_time = 0
        self.on_time = None
        self.on = None

        self.get_data()

    def _rpc(self, endpoint, id=0, on=None):
        endpoint = f'http://{self._host}/rpc/{endpoint}?id={id}'
        if on is not None:
            if on:
                endpoint += '&on=true'
            else:
                endpoint += '&on=false'
        self.log.info(f'endpoint is {endpoint}')
        try:

            res = urllib.request.urlopen(endpoint)
        except urllib.error.URLError:
            return None
        data = res.read()
        return json.loads(data)

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

    def get_data(self):
        data = self._rpc('Switch.GetStatus')
        if not data:
            self.log.info('Failed to load data')
            return
        self.voltage = data['voltage']
        self.watts = data['apower']
        self.have_energy = True
        on = data['output']
        if self.on is not None and on != self.on:
            self.log.debug('Socket %s state changed externally from %s to %s',
                           self.name, self.on, on)
            self.external_change = True
            self.ec_time = time.time()
        self.on = on

    def get_power(self):
        if self.watts > 0:
            return self.watts
        return 100

    def turn_on(self):
        self._rpc('Switch.Set', on=True)
        self.external_change = False
        self.on = True
        self.log.info("Turned on '%s'", self.name)

    def turn_off(self):
        self._rpc('Switch.Set', on=False)
        self.external_change = False
        self.on = False
        self.log.info("Turned off '%s'", self.name)

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
        return self.pm.kwh()
