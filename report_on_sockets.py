#!/usr/bin/python3

"""Show the status and logs of network sockets"""

import run_zappi
import mec.tpsockets

def main():
    """main"""

    config = run_zappi.load_config()

    for socket in config['sockets']:
        obj = mec.tpsockets.PowerSocketConnection(socket['ip'])
        obj.load_todays_power()
        obj.get_data()
        print(obj)
        obj.read_igain()

if __name__ == '__main__':
    main()
