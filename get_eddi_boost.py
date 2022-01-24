#!/usr/bin/python3

"""Show the boost status"""

import run_zappi
import mec.zp

def main():
    """Main"""

    config = run_zappi.load_config()

    server_conn = mec.zp.MyEnergiHost(config['username'], config['password'])
    server_conn.refresh()

    for eddi in server_conn.state.eddi_list():
        print('Boost schedule for Eddi {}'.format(eddi.sno))
        server_conn.get_boost(eddi.sno)

if __name__ == '__main__':
    main()
