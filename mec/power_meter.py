#!/usr/bin/python3

"""Class for monitoring power usage"""

import time

import logging

class PowerMeter():
    """Power meter for working out consumed power over time"""

    log = logging.getLogger(__name__)

    def __init__(self, name=None):

        # Value seconds, for positive values only.
        self.name = name
        self.value = 0
        # Value seconds, for negative values only.
        self.neg_value = 0
        # Most recent sample time.
        self._prev_time = None
        self._have_data = False

    def __str__(self):
        if self.neg_value:
            return '{:.3f}kWh -{:.3f}kWh'.format(self.value / (60*60*1000),
                                                 self.neg_value / (60*60*1000))
        return '{:.3f}kWh'.format(self.value / (60*60*1000))

    def add_value(self, value, sample_time):
        """Add a value, with timestamp."""

        if isinstance(sample_time, int):
            stime = sample_time
        else:
            stime = time.mktime(sample_time)
        if not self._have_data:
            self._prev_time = stime
            self.log.debug('Setting start time as %s', stime)
            self._have_data = True
            return
        elapsed = stime - self._prev_time
        self._prev_time = stime
        self.log.debug('Adding %s for %d seconds', value, elapsed)
        if value > 0:
            self.value += (value * elapsed)
        else:
            self.neg_value -= (value * elapsed)

    def kwh(self):
        """Return the positive power value, in kWh"""
        return self.value / (60*60*1000)

    def nkwh(self):
        """Return the negative power value, in kWh"""
        return self.neg_value / (60*60*1000)

    def reset_value(self, kwh=0):
        """Reset timer to zero"""
        new_value = kwh * (60*60*1000)
        self.log.debug("Resetting '%s' to %.3f %s", self.name, new_value / (60*60*1000), str(self))
        self.value = new_value
        self.neg_value = 0
        self._have_data = False

    def __del__(self):
        self.reset_value()
