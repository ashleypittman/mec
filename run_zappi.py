#!/usr/bin/python3

"""User command for monitoring Zappi"""

import os.path
import time
import sys
import logging
import logging.handlers
import datetime
from collections import OrderedDict
import yaml
from ascii_graph import Pyasciigraph

# Local imports.
import mec.tpsockets
import mec.display
import mec.zp
import mec.power_meter
import mec.session

RC_FILE = '~/.zappirc'

DELAY = 60

def setup_logging(debug):
    """Configure global logging state"""

    if not debug:
        return
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Log info to stdout natively.
    channel = logging.StreamHandler()
    oformat = logging.Formatter()
    channel.setLevel(logging.INFO)
    channel.setFormatter(oformat)
    root.addHandler(channel)

    # Log debug to file, and add prefix.
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'logs', 'myenergi.log')
    if not os.path.exists('logs'):
        os.mkdir('logs')
    channel = logging.handlers.TimedRotatingFileHandler(log_file)
    my_pid = os.getpid()
    mformat = '%(asctime)s - {} - %(name)s - %(levelname)s - %(message)s'.format(my_pid)
    oformat = logging.Formatter(mformat)
    channel.setLevel(logging.DEBUG)
    channel.setFormatter(oformat)
    root.addHandler(channel)

# Get logging handle for this file.
log = logging.getLogger('run_zappi')

def load_config(debug=True):
    """Load the config file and return dict"""
    setup_logging(debug)
    ofh = open(os.path.expanduser(RC_FILE), 'r')
    return yaml.safe_load(ofh)

def main():
    """Main"""

    config = load_config()

    sockets = []

    if 'sockets' in config:
        for socket in config['sockets']:
            obj = mec.tpsockets.PowerSocketConnection(socket['ip'])
            if 'mode' in socket:
                obj.mode = socket['mode']
            if 'power' in socket:
                obj.set_initial_power(socket['power'])
            if 'night' in socket and socket['night']:
                obj.on_time = datetime.datetime(year=1977, month=1, day=1,
                                                hour=0, minute=15)
                obj.duration = datetime.timedelta(hours=4)
            obj.load_todays_power()
            sockets.append(obj)

    if 'house_data' in config:
        house_conf = config['house_data']
    else:
        house_conf = {}
    server_conn = mec.zp.MyEnergiHost(config['username'],
                                      config['password'],
                                      house_conf)

    try:
        if len(sys.argv) == 2:
            if sys.argv[1] == 'once':
                show_zappi_data(config, server_conn, sockets)
            return
        log.debug('Starting server')
        session_engine = mec.session.SessionEngine(config)
        display = mec.display.ePaper(config)
        run_loop(server_conn, sockets, session_engine, display)
    except KeyboardInterrupt:
        log.exception('KeyboardInterrupt')
        for socket in sockets:
            socket.get_data()
            if not socket.on:
                continue
            if socket.mode in ['auto']:
                socket.turn_off()
    except Exception:
        log.exception('Exception')
        for socket in sockets:
            socket.get_data()
            if not socket.on:
                continue
            if socket.mode in ['auto']:
                socket.turn_off()
        raise

def show_zappi_data(config, server_conn, sockets):
    """Show the current state and return"""
    for socket in sockets:
        socket.get_data()
    while True:
        try:
            server_conn.refresh()
            print(server_conn.state.report(sockets))
            break
        except mec.zp.DataException:
            pass
    state = server_conn.state
    for zappi in state.zappi_list(priority_order=True):
        print('Priority for {} is {}'.format(zappi.sno, zappi.priority))
        print(zappi.report())
    for harvi in state._harvis:
        print(harvi.report())

    if False:
        session_engine = mec.session.SessionEngine(config)
        session = session_engine.new_session()
        session.update(2)
        session = None

    for line in get_graph(state, sockets):
        print(line)
    for (key, value, _) in state.get_readings():
        print("'{}' is {}".format(key, value))

def get_graph(state, sockets):
    """Return a ascii graph of the current consumption"""

    display_order = ['Grid', 'Generation', 'Zappi', 'iBoost', 'House']
    gdata = []
    house_delta = 0
    grid_index = -1
    grid_power = 0
    for key in display_order:
        if key == 'Zappi':
            for zappi in state.zappi_list(priority_order=True):
                if zappi.charge_rate:
                    gdata.append(('Zappi({})'.format(zappi.sno), zappi.charge_rate))
        elif key in state._values and state._values[key] != 0:
            value = state._values[key]
            if key.startswith('G'):
                value = -value
            if key in ('Generation', 'iBoost') and abs(value) < 30:
                house_delta += value
                continue
            if key == 'House':
                value += house_delta
            if key == 'Grid':
                grid_index = len(gdata)
                grid_power = value
            gdata.append((key, value))

    for socket in sockets:
        if not socket.have_energy:
            continue
        if not socket.on:
            grid_power += socket.watts
            continue
        gdata.append((socket.name, socket.watts))
    for key in sorted(state._values):
        if key in display_order:
            continue
        gdata.append((key, state._values[key]))
    gdata[grid_index] = ('Grid', grid_power)

    graph = Pyasciigraph(separator_length=1)
    return graph.graph(data=gdata)


class LoopFns():

    """Progress the inner loop"""

    def __init__(self, log, server_conn, sockets, session_engine, display):
        self.log = log
        self.server_conn = server_conn
        self.sockets = sockets
        self.se = session_engine
        self.display = display
        self.sessions = {}
        self.auto_eco = set()

    def resample(self):
        """Resample all data"""
        # Refresh all data.  If it appears bogus then wait for a bit and resample
        self.server_conn.refresh(check=True)
        for socket in self.sockets:
            socket.get_data()

    def loop(self, culm_values):
        """Invoke all progress functions"""

        # Update the session manager.

        self._try_update_sm()

        self.display.sample(self.server_conn, self.sessions, culm_values, self.sockets)

        self._check_and_set_timers()

        self._reset_mode_if_idle()

        self._new_power_divert()

        self.display.update()

    def in_time_window(self, now, start, duration):
        if now.tm_hour < start.hour:
            return False
        elif now.tm_hour == start.hour and now.tm_min < start.minute:
            return False
        end_time = start + duration
        if now.tm_hour > end_time.hour:
            return False
        if now.tm_hour == end_time.hour and now.tm_min > end_time.minute:
            return False
        return True

    def _check_and_set_timers(self):

        now = time.gmtime()
        for socket in self.sockets:
            if not socket.on_time:
                continue
            if socket.mode == 'timed':
                if not self.in_time_window(now, socket.on_time, socket.duration):
                    socket.turn_off()
                    self.log.info('Turned off {} from timer'.format(socket.name))
                    socket.mode = 'auto'
            elif self.in_time_window(now, socket.on_time, socket.duration):
                socket.turn_on()
                self.log.info('Turned on {} from timer'.format(socket.name))
                socket.mode = 'timed'

    def _try_update_sm(self):
        state = self.server_conn.state

        have_leaf = False
        for session in self.sessions.values():
            if session['se'].session and session['se'].session._is_leaf:
                have_leaf = True
        for zappi in state.zappi_list():
            if zappi.sno not in self.sessions:
                self.sessions[zappi.sno] = {}
                self.sessions[zappi.sno]['se'] = mec.session.SessionManager(self.se)
                self.sessions[zappi.sno]['low_charge'] = False
            sm = self.sessions[zappi.sno]['se']
            sm.update_state(state, zappi, have_leaf)
            if sm.should_health_charge() and zappi.mode == 'Eco+':
                self.server_conn.set_mode_eco(zappi.sno)
                self.sessions[zappi.sno]['low_charge'] = True
            elif self.sessions[zappi.sno]['low_charge'] and zappi.mode == 'Eco' and not sm.should_health_charge():
                self.server_conn.set_mode_ecop(zappi.sno)
                self.sessions[zappi.sno]['low_charge'] = False
            elif zappi.mode != 'Fast' and sm.should_stop_charge():
                self.log.info('Stopping charge as battery full')
                self.server_conn.set_mode_stop(zappi.sno)

    def _reset_mode_if_idle(self):
        """Reset Zappi to eco+ if idle"""
        state = self.server_conn.state
        for zappi in state.zappi_list():
            if zappi.car_connected() and zappi.status != 'Hot':
                continue
            if zappi.mode != 'Eco+':
                self.server_conn.set_mode_ecop(zappi.sno)
            if zappi.min_green_level != 100:
                self.server_conn.set_green_level(100, zappi.sno)

    def _new_power_divert(self):

        devices = []
        sockets = []

        for socket in self.sockets:
            if socket.mode != 'auto':
                continue
            self.log.debug('considering socket %s %s %s %s', socket.name, socket.external_change, socket.on, socket.on_time)
            if (socket.external_change and socket.on) or socket.on_time:
                devices.append(socket)
            else:
                sockets.append(socket)
        state = self.server_conn.state
        available_power = state._values['Generation']
        available_power -= state._values['House']
        car_first = False
        can_auto_eco = False
        if not car_first:
            devices.append('iBoost')
        for zappi in state.zappi_list(priority_order=True):
            if zappi.sno in self.auto_eco and zappi.mode != 'Eco':
                self.auto_eco.remove(zappi.sno)
            if zappi.car_connected() and not zappi.status == 'Hot' and zappi.mode != 'Fast':
                can_auto_eco = True
                devices.append(zappi)
            if zappi.mode == 'Fast':
                available_power -= zappi.charge_rate
        if car_first:
            devices.append('iBoost')
        devices.extend(sockets)

        if 'iBoost' not in state._values:
            devices.remove('iBoost')

        self.log.debug(state._values)
        self.log.debug('Available power is %d', available_power)
        self.log.debug('Auto eco is %s', self.auto_eco)
        self.log.debug(devices)
        fast_off = False
        first_device = True
        set_eco = False
        for device in devices:
            self.log.debug('Checking %s for %d watts', device, available_power)
            if isinstance(device, mec.zp.Zappi):
                zappi = device
                # This is difficult to call, there are two options to consider
                # here:
                # The car is charging slowly, most likely due to battery
                # balancing in the outlander.
                # The car is charging, and would charge faster if there was
                # power available.
                # To fix this always allow for the maximum of the minimum it
                # needs, and what it's currently consuming.  If the iBoost is
                # on then the Zappi will scavange from this anyway so no
                # adjustment is needed, however if the iBoost is not using any
                # power then allow 250w for the charge rate to increase as
                # over time this should turn off any sockets preventing this.
                if zappi.sno in self.auto_eco and 'iBoost' in state._values:
                    # TODO: This value needs checking.
                    if (available_power + state._values['iBoost'] + zappi.charge_rate) < 1500:
                        self.log.info('Setting Zappi to eco+ for %s %d %d %d', zappi.sno, available_power, state._values['iBoost'], zappi.charge_rate)
                        self.server_conn.set_mode_ecop(zappi.sno)
                        #self.auto_eco.remove(zappi.sno)
                if available_power >= zappi.min_charge_rate_with_level() or zappi.charge_rate > 0:
                    available_power -= max(zappi.charge_rate,
                                           zappi.min_charge_rate_with_level())
                    if zappi.waiting_for_export():
                        available_power -= 1000
                        fast_off = True
                    elif car_first:
                        if state._values['iBoost'] < 100:
                            available_power -= 250
                    else:
                        # Give some headway for the car to charge faster.
                        available_power -= 250
                continue
            elif device == 'iBoost':
                # The iBoost may be satisified so only attribute power to it
                # if it is currently consuming anything
                if can_auto_eco and available_power > 2000:
                    set_eco = True
                if state._values['iBoost'] > 50:
                    if car_first:
                        available_power -= 2000
                    else:
#                        available_power -= state._values['iBoost']
                        # This should ideally be in amps.
                        available_power -= 2000
                continue
            if device.on:
                if available_power > device.watts:
                    device.reset_strike_count()
                else:
                    if fast_off:
                        device.turn_off()
                    else:
                        device.strike()
                    if not device.on:
                        self.log.info("Saved %d watts by turning off '%s'", device.watts, device.name)
                # Always decrease the available power, in case of lower priority diverters.
                available_power -= device.watts

            # Only run this check once, for the first device.
            elif first_device and available_power > device.get_power() and not set_eco:
                device.turn_on()
                self.log.info("Turned on '%s' to use %d watts", device.name, device.get_power())
                available_power -= device.get_power()
                first_device = False
            else:
                self.log.debug("Device '{}' needs {} watts".format(device.name, device.get_power()))
                first_device = False
        if set_eco:
            for zappi in state.zappi_list(priority_order=True):
                if not zappi.car_connected() or zappi.mode != 'Eco+':
                    continue
                self.log.info('Setting Zappi to eco for %s', zappi.sno)
                self.server_conn.set_mode_eco(zappi.sno)
                self.auto_eco.add(zappi.sno)
                break

def run_loop(server_conn, sockets, session_engine, display):
    """Run in a loop"""
    start_time = time.time()
    total_delay = 0
    now_time = time.localtime()
    yday = now_time.tm_yday
    culm_values = OrderedDict()
    loop_handler = LoopFns(log, server_conn, sockets, session_engine, display)
    while True:

        # Refresh all data.  If it appears bogus then wait for a bit and resample
        try:
            loop_handler.resample()
        except mec.zp.DataException:
            time.sleep(5)
            continue

        now_time = time.localtime()
        if now_time.tm_yday != yday:
            log.info('New day detected')
            for key in culm_values:
                culm_values[key].reset_value()
            yday = now_time.tm_yday
            for socket in sockets:
                socket.reset_day()

        # Print new output
        print()
        print(time.asctime(now_time))

        state = server_conn.state
        print(state.report(sockets))

        for (key, value, stime) in state.get_readings():
            if key not in culm_values:
                culm_values[key] = mec.power_meter.PowerMeter(key)
            culm_values[key].add_value(value, stime)
        for key in culm_values:
            log.info('Total for %s is %s', key, culm_values[key])

        for line in get_graph(state, sockets):
            log.info(line)

        loop_handler.loop(culm_values)

        # If car is charging and mode is Eco+ and div < 1.4 then set mode to eco, with
        # ability to raise it again when div > 1.4

        # Use an adjustable delay so that it wakes up at the same time each iteration,
        # this means if it takes non-zero time to do any work then the next loop starts
        # at the correct time still.  Refer back to the initial start time each loop
        # to avoid gradual drift.
        # Handle the case where time has already elapsed.
        while True:
            now = time.time()
            total_delay += DELAY

            delay = (start_time + total_delay) - now
            if delay > 0:
                break
        time.sleep(delay)

if __name__ == '__main__':
    main()
