#!/usr/bin/env python3

# Set the Leaf to charge overnight.

import sys
import time
import argparse

import run_zappi
import mec.zp
import mec.agile
import mec.session

def main():
    """Main"""

    parser = argparse.ArgumentParser(description='Configure Zappi boost times')
    parser.add_argument('--charge', type=float, help='Number of KwH to charge', default=10)
    parser.add_argument('--rate', type=int, help='Charge rate of car in Watts', default=7200)
    parser.add_argument('--by-hour', type=int, help='Finish by this hour', default=8)
    parser.add_argument('--target-soc', type=int, help='Charge car to SOC', default=0)
    parser.add_argument('--reset', help='Clear all boost values', action='store_true')
    parser.add_argument('--sno', type= int, help='Serial number of Zappi to boost', default=0)
    parser.add_argument('--dry-run', help='Show would would be set, but do not set it', action='store_true')
    args = parser.parse_args()

    config = run_zappi.load_config()

    # Set to true if car is to charge to 100%, this allows extra time
    # for reduced current at high SOC values.
    extra_time = False

    if args.target_soc != 0:
        if args.target_soc > 100:
            print('Cannot charge above 100')
            return
        if args.target_soc > 95:
            args.target_soc = 100
        sm = mec.session.SessionEngine(config)
        se = sm.new_session()
        if not isinstance(se, mec.session.CommonSession):
            print('Cannot connect to car')
            return
        se.check_connected = False
        se.update(0)
        while (se._is_valid is None):
            # The Leaf API only updates every 20 seconds, so wait a little bit
            # more than that, and re-sample.
            time.sleep(21)
            se.update(0)
        if not se._is_valid:
            print('Could not detect car')
            return
        percent = se.percent_charge()
        print('Percent charge is {}'.format(percent))
        if percent > args.target_soc:
            print('Car already has enough charge')
            args.reset = True
        to_add = se.charge_required_for_soc(args.target_soc)
        charge_rate = se.charge_rate
    else:
        # KwH to add
        to_add = args.charge
        # Charge rate
        charge_rate = args.rate

    if args.reset:
        print('Will wipe all timers')
    else:
        print('Will aim to add {:2.1f}kWh at {:.1f}kW by {}am'.format(to_add,
                                                                 charge_rate/1000,
                                                                 args.by_hour))


    # Now work out how long is needed to charge.
    # Agile charge rates are per 30 minutes, but Zappi
    # schedules are per 15 minutes.  Ignore this and just
    # do everything by 30 minute window.  This gives
    # an overestimate each time so is safe, and is
    # unlikely to cost much extra.
    time_needed = to_add/(charge_rate/1000)
    if extra_time:
        time_needed += 2
    sessions_needed = time_needed * 2
    sn = int(sessions_needed) + 1
    slots = mec.agile.pick_slots(config, args.by_hour, sn, 4)

    if args.dry_run:
        for slot in slots.ranges:
            print('Would charge for {}'.format(slot))
        price = slots.get_price()
        total_price = charge_rate / 1000 * price
        print('Charge would cost {:.1f} pence'.format(total_price))
        return

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()

    for zappi in server_conn.state.zappi_list():
        if args.sno == 0 or args.sno == zappi.sno:
            print('Zappi is currently in mode {}'.format(zappi.mode))

            SIDS=[11,12,13,14]
            if not args.reset:
                if not zappi.car_connected():
                    print('Setting boost times without car connected?')

                for slot in slots.ranges:
                    duration = slot.duration()
                    server_conn.set_boost(zappi.sno, SIDS.pop(),
                                          bsh=slot.start_time.tm_hour,
                                          bsm=slot.start_time.tm_min,
                                          dow=slot.start_time.tm_wday,
                                          bdh=duration // 60,
                                          bdm=duration % 60)
                price = slots.get_price()
                total_price = charge_rate / 1000 * price
                print('Estimated charge cost {:.1f} pence'.format(total_price))

            # Now clear any other boost timers.
            for zslot in SIDS:
                server_conn.set_boost(zappi.sno, zslot)

if __name__ == '__main__':
    sys.exit(main())
