"""Module for setting Eddi boost timers"""

import logging


class EddiBoost:
    """Class for setting the Eddi boost"""

    log = logging.getLogger(__name__)

    def __init__(self, server_conn):
        self._sc = server_conn
        self.desired_temp = 40
        self._in_time_window = False
        self._heater = 2

    def _stop_boost(self, eddi):
        self.log.info('Stopping boost')
        self._sc.stop_eddi_boost(eddi.sno, self._heater)

    def _cur_temp(self, eddi):
        if self._heater == 1:
            return eddi.temp_1
        return eddi.temp_2

    def _check_for_boost_start(self, eddi):

        if self._cur_temp(eddi) < self.desired_temp:
            self.log.info('Starting boost')
            self._sc.start_boost(eddi.sno, self._heater, 60)
        else:
            self.log.info('Temp reached')

    def run(self, eddi, in_time_window):
        """Run periodically"""

        self.log.info('Updating: In time window: {}'.format(in_time_window))
        if in_time_window and not self._in_time_window:
            self._in_time_window = True
            self._check_for_boost_start(eddi)
            return

        if not in_time_window:
            if self._in_time_window:
                self._stop_boost(eddi)
                self._in_time_window = False
            return

        if eddi.charge_rate == 0:
            return

        if eddi.status != 'Boost':
            return

        if self.desired_temp < self._cur_temp(eddi):
            self.log.info('Desired temp reached')
            self._stop_boost(eddi)
