#!/usr/bin/python3

from functools import total_ordering

import urllib.request
import logging
import json
import time

# Thanks to https://developer.octopus.energy/docs/api/#

BASE_URL='https://api.octopus.energy'
PRODUCT_CODE='AGILE-18-02-21'

log = logging.getLogger('agile')

@total_ordering
class AgileSlot ():
    # A single 30 minute agile timeslot.

    def __init__(self, raw):
        self.price = raw['value_inc_vat']
        # Agile data is published in GMT, record it as such with correct
        # timezone data, then covert it to localtime from here on in.
        st = raw['valid_from'][:-3] + 'GMT'
        self.start_time = time.strptime(st, '%Y-%m-%dT%H:%M:%Z')
        self.start_time = time.localtime(time.mktime(self.start_time))
        st = raw['valid_to'][:-3] + 'GMT'
        self.end = time.strptime(st, '%Y-%m-%dT%H:%M:%Z')
        self.end = time.localtime(time.mktime(self.end))
        self.slot_count = 1

    def __lt__(self, a):
        return self.start_time < a

    def __str__(self):
        st = self.start_time
        return '{:6.2f}p {:02d}:{:02d}'.format(self.price, st.tm_hour, st.tm_min)

class AgileRange():
    # A range of multiple, contiguous agile timeslots.

    def __init__(self, slot):
        self.slots = [slot]
        self.start_time = slot.start_time
        self.end = slot.end
        # Total price for all slots.
        self.price = slot.price
        self.slot_count = 1

    def add(self, slot):
        # Try to expand the current range to add a new timeslot,
        # return False if the slot isn't adjacent to the range.
        if self.end == slot.start_time:
            self.slots.append(slot)
            self.end = slot.end
            self.slot_count += slot.slot_count
            self.price += slot.price
            return True

        if self.start_time == slot.end:
            self.slots.append(slot)
            self.start_time = slot.start_time
            self.slot_count += slot.slot_count
            self.price += slot.price
            return True

        return False
    
    def duration(self):
        # Return the duration of the range, in minutes.
        # TODO: write a unit test for this function.
        duration = 0
        hours = self.end.tm_hour - self.start_time.tm_hour
        if hours < 0:
            hours += 24
        duration = hours * 60
        minutes = self.end.tm_min - self.start_time.tm_min
        duration += minutes
        assert duration == self.slot_count * 30
        return duration

    def __repr__(self):
        return str(self)

    def __str__(self):
        st = self.start_time
        end = self.end
        return '{:02d}:{:02d} {:02d}:{:02d} {}'.format(st.tm_hour,
                                                       st.tm_min,
                                                       end.tm_hour,
                                                       end.tm_min,
                                                       self.duration())

def get_current_data(conf):
    # Return an array of all future timeslots, including the
    # current one.

    now = time.localtime()

    all_future_data = []

    try:
        region = conf['agile']['region']
    except KeyError:
        region = 'F'

    tarrif_code='E-1R-{}-{}'.format(PRODUCT_CODE, region)
    data_url='{}/v1/products/{}/electricity-tariffs/{}/standard-unit-rates'.format(BASE_URL,
                                                                                   PRODUCT_CODE,
                                                                                   tarrif_code)

    done = False
    while not done:
        raw = urllib.request.urlopen(data_url)

        data = json.load(raw)
        data_url = data['next']
        for row in data['results']:
            n = AgileSlot(row)
            all_future_data.append(n)
            if n < now:
                done = True
                break
    return list(reversed(all_future_data))

def get_slots_until_time(conf, hour):
    # Return all timeslots from now, until the specified
    # hour.

    # This function is not without issues, it's intended to
    # be called in the evening to setup overnight charging,
    # so assumes that 'hour' has already passed for today.
    data = get_current_data(conf)

    past_midnight = False
    slots = []
    for row in data:

        if row.start_time.tm_hour == 0:
            past_midnight = True
        if row.start_time.tm_hour >= hour and past_midnight:
            break
        slots.append(row)

    return sorted(slots, key=lambda z: z.price)

class TimeWindows():
    # Manage a bound number of time windows, or AgileRanges.

    def __init__(self, window_count):
       self.window_count = window_count
       self.ranges = []

    def try_add(self, slot):
        # Try to add a timeslot to an existing range, or create
        # a new one if there is space.
        for rng in self.ranges:
            if rng.add(slot):
                self._do_merge()
                return True
        if len(self.ranges) < self.window_count:
            self.ranges.append(AgileRange(slot))
            return True
        return False

    def _do_merge(self):
        new_ranges = []
        for rng in self.ranges:
            added = False
            for nr in new_ranges:
                if nr.add(rng):
                    added = True
                    break
            if not added:
                new_ranges.append(rng)
        self.ranges = new_ranges

    def sort_by_time(self):
        """Re-order time ranges to be sorted by time"""

        self.ranges = sorted(self.ranges, key=lambda s: s.start_time)

    def get_price(self):
        """Return the unit cost of electricity, not accounting
        for rate.  Multiply by charge rate to get cost.

        slot.price is the sum of all slot prices for this range,
        so just use it, duration is already factored in, however
        each slot is only 30 minutes long so reduce the overall
        price by 2
        """

        price = 0
        for slot in self.ranges:
            price += slot.price / 2
        return price

def pick_slots(conf, end_hour, count, windows):
    # Pick a number of Time slots.
    # count is the number of time slots that are required
    # windows is how many boost settings there are.
    log.debug('Looking for %d slots by %d in %d windows', count, end_hour, windows)
    slots = get_slots_until_time(conf, end_hour)

    # This is fairly simple, and could be persuaded to fail by
    # a carefully constructed set of inputs, simply walk the
    # list of time slots by preference trying each one.
    # Due to a limited number of windows it may not be optional.
    # Continue in a loop until there are no more slots, or
    # enough are used.
    # It would probaby be better to restart from the start of
    # the initial list after every addition, in order to better
    # benefit from coalescing.
    tw = TimeWindows(windows)
    added = 0
    while slots and added != count:
       for slot in slots:
           if tw.try_add(slot):
               log.debug('Added %s', slot)
               slots.remove(slot)
               added += 1
               break

    if added != count:
        log.info('Wanted %d slots but only found %d', count, added)
    tw.sort_by_time()
    for slot in tw.ranges:
        log.debug('slot is %s', slot)
    return tw

if __name__ == '__main__':
    # Before ten AM pick seven slots in four windows.
    pick_slots(None, 10, 7, 4)
