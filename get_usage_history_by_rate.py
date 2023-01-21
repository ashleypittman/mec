#!/usr/bin/env python3

"""get the history of usage"""

import datetime
from datetime import date
import getopt
import sys

import tabulate

import run_zappi
import mec.zp
import mec.power_meter

# This needs to have debugging disabled.

FIELD_NAMES = {'gep': 'Generation',
               'gen': 'Generated Negative',
               'h1d': 'Zappi diverted',
               'h1b': 'Zappi imported',
               'imp': 'Imported',
               'exp': 'Exported'}


class Day:
    def __init__(self, year, month, day):
        self.tm_year = year
        self.tm_mon = month
        self.tm_mday = day


class Rate:
    def __init__(self, endtime, name, label=""):
        self.name = name
        self.label = label
        self.endtime = datetime.datetime.strptime(endtime, "%H:%M")


show_headers = True


def main():
    """Main"""
    global show_headers

    args = [
        'start=',
        'end=',
        'battery=',
    ]

    try:
        opts, args = getopt.getopt(sys.argv[1:], '', args)
    except getopt.GetoptError:
        print('Unknown options')
        print(args)
        sys.exit(2)

    # hardcoded as not sure best to encode to pass in as rates are typically
    # described as overlapping times when more than just day/night:
    #  23:00-08:00 night rate
    #  02:00-05:00 ev rate
    #  08:00-23:00 day rate
    #  17:00-19:00 peak rate
    rate_times = [
        Rate("02:00", "night-pre-ev", "night"),
        Rate("05:00", "ev"),
        Rate("08:00", "night-post-ev", "night"),
        Rate("17:00", "day-pre-peak", "day"),
        Rate("19:00", "peak"),
        Rate("23:00", "day-post-peak", "day"),
    ]
    usage = {rate_time.name: 0 for rate_time in rate_times}
    battery = 0

    start = date.today()
    end = date.today()

    for opt, value in opts:
        if opt == '--start':
            start = date.fromisoformat(value)
        elif opt == '--end':
            end = date.fromisoformat(value)
        elif opt == '--battery':
            battery = int(value)

    config = run_zappi.load_config(debug=False)

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()

    # The Zappi V2.
    while start <= end:
        day = Day(start.year, start.month, start.day)
        day_usage = {rate_time.name: 0 for rate_time in rate_times}

        for zappi in server_conn.state.zappi_list():
            show_headers = True

            _, data, _ = load_day(server_conn, zappi.sno, day, True, False, True)
            for block in data[:-1]:
                time_slot = datetime.datetime.strptime(block[0], "%H:%M")
                imported = block[2]

                if imported is None:
                    continue

                for rate_time in rate_times:
                    # per hour rate is the averaged watts for the following 59 minutes
                    # so it should be compared as less than against the end time since
                    # once it is equal, it corresponds to the following hour.
                    if time_slot < rate_time.endtime:
                        day_usage[rate_time.name] += imported
                        break
                else:
                    day_usage[rate_times[0].name] += imported

        if battery > 0:
            # charge battery from ev slot for morning
            reduction = min(battery, day_usage['day-pre-peak'])
            if reduction > 0:
                day_usage['ev'] += reduction
                day_usage['day-pre-peak'] -= reduction

            # charge battery from day rate for peak rate time
            reduction = min(battery, day_usage['peak'])
            if reduction > 0:
                day_usage['day-pre-peak'] += reduction
                day_usage['peak'] -= reduction

        for name, consumption in day_usage.items():
            usage[name] += consumption

        start += datetime.timedelta(days=1)

    # filter down to the minimum set of rates
    for rate in rate_times:
        if rate.label != "":
            if rate.label not in usage:
                usage[rate.label] = usage[rate.name]
            else:
                usage[rate.label] += usage[rate.name]
            del usage[rate.name]

    for rate_name, consumption in usage.items():
        # convert watts per hour to kWh
        print("{:8}: {:.2f} kWh".format(rate_name, consumption/1000))


def load_day(server_conn, zid, day, hourly, totals, use_json):

    global show_headers

    if hourly:
        res = server_conn.get_hour_data(zid, day=day)
        prev_sample_time = - 60 * 60
    else:
        res = server_conn.get_minute_data(zid, day=day)
        prev_sample_time = -60

    headers = ['imp', 'exp', 'gen', 'gep', 'h1d', 'h1b',
               'pect1', 'nect1', 'pect2', 'nect2', 'pect3', 'nect3']
    table_headers = ['Time', 'Duration']
    data = []
    pm_totals = {}
    for key in headers:
        pm_totals[key] = mec.power_meter.PowerMeter()
        pm_totals[key].add_value(0, prev_sample_time)
        if key in FIELD_NAMES:
            table_headers.append(FIELD_NAMES[key])
        else:
            table_headers.append(key)
    for rec in res:
        row = []
        hour = 0
        minute = 0
        volts = 1
        if 'imp' in rec and 'nect1' in rec and rec['imp'] == rec['nect1']:
            del rec['nect1']
        if 'exp' in rec and 'pect1' in rec and rec['exp'] == rec['pect1']:
            del rec['pect1']
        if 'hr' in rec:
            hour = rec['hr']
            del rec['hr']
        if 'min' in rec:
            minute = rec['min']
            del rec['min']

        sample_time = ((hour * 60) + minute) * 60

        for key in ['dow', 'yr', 'mon', 'dom']:
            del rec[key]

        if 'v1' in rec:
            volts = rec['v1'] / 10
        for key in ['v1', 'frq']:
            if key in rec:
                del rec[key]

        row.append('{:02}:{:02}'.format(hour, minute))
        row.append(sample_time - prev_sample_time)

        for key in headers:
            if key in rec:
                value = rec[key]
                if hourly:
                    watts = value / (60 * 60)
                else:
                    watts = value / volts * 4
                row.append(int(watts))
                del rec[key]
            else:
                watts = 0
                row.append(None)
            pm_totals[key].add_value(watts, sample_time)
        prev_sample_time = sample_time

        if rec:
            print(rec)
        data.append(row)
    num_records = len(data)
    if not use_json:
        print('There are {} records'.format(num_records))
    if totals:
        data = []
    row = ['Totals', None]
    for key in headers:
        row.append(str(pm_totals[key]))
    data.append(row)

    if not use_json:
        if show_headers:
            print(tabulate.tabulate(data, headers=table_headers))
            show_headers = False
        else:
            print(tabulate.tabulate(data))
    return table_headers, data, row


if __name__ == '__main__':
    main()
