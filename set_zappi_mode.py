#!/usr/bin/python3

"""Set the mode of the Zappi to eco+"""

import run_zappi
import mec.zp

def main():
    """Main"""

    config = run_zappi.load_config()

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()

    for zappi in server_conn.state.zappi_list():
        print('Zappi is currently in mode {}'.format(zappi.mode))
        #print(server_conn.set_mode_ecop(zappi.sno))
        print(server_conn.set_mode_stop(zappi.sno))

if __name__ == '__main__':
    main()
