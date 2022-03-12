#!/usr/bin/env python3

"""Show the boost status"""

import run_zappi
import mec.zp


def main():
    """Main"""

    config = run_zappi.load_config()

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()

    for zappi in server_conn.state.zappi_list():
        print('Boost schedule for Zappi {}'.format(zappi.sno))
        server_conn.get_boost(zappi.sno)

        # server_conn.stop_boost(zappi.sno)


if __name__ == '__main__':
    main()
