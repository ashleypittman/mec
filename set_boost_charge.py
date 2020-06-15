#!/usr/bin/python3

# Set the Leaf to charge overnight.

import sys
import time
import argparse

import run_zappi
import mec.zp
import mec.agile

def main():
    """Main"""

    parser = argparse.ArgumentParser(description='Configure Zappi boost times')
    parser.add_argument('--charge', type=float, help='Number of KwH to charge', default=10)
    parser.add_argument('--rate', type=int, help='Charge rate of car in Watts', default=7200)
    parser.add_argument('--by-hour', type=int, help='Finish by this hour', default=8)
    parser.add_argument('--reset', help='Clear all boost values', action='store_true')
    args = parser.parse_args()

    if args.reset:
        print('Will wipe all timers')
    else:
        print('Will aim to add {}kWh at {:.1f}kW by {}am'.format(args.charge,
                                                                 args.rate/1000,
                                                                 args.by_hour))
    config = run_zappi.load_config()

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()


    # KwH to add
    to_add = args.charge
    # Charge rate
    charge_rate = args.rate

    # Now work out how long is needed to charge.
    # Agile charge rates are per 30 minutes, but Zappi
    # schedules are per 15 minutes.  Ignore this and just
    # do everything by 30 minute window.  This gives
    # an overestimate each time so is safe, and is
    # unlikely to cost much extra.
    time_needed = to_add/(charge_rate/1000)
    sessions_needed = time_needed * 2
    sn = int(sessions_needed + 0.5)
    slots = mec.agile.pick_slots(args.by_hour, sn, 4)

    for zappi in server_conn.state.zappi_list():
        print('Zappi is currently in mode {}'.format(zappi.mode))

        SIDS=[11,12,13,14]
        if not args.reset:
            if not zappi.car_connected():
                print('Setting boost times without car connected?')

            for slot in slots.ranges:
                duration = slot.duration()
                server_conn.set_boost(zappi.sno, SIDS.pop(), bdd='11111111',
                                      bsh=slot.start_time.tm_hour,
                                      bsm=slot.start_time.tm_min,
                                      bdh=duration // 60,
                                      bdm=duration % 60)

        # Now clear any other boost timers.
        for zslot in SIDS:
            server_conn.set_boost(zappi.sno, zslot) 

if __name__ == '__main__':
    sys.exit(main())
