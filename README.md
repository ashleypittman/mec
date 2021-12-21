Python client and API for monitoring and controling energy diversion devices from [MyEnergi](https://myenergi.com/)

A set of library functions and objects for interfacing with the cloud servers of MyEnergi to monitor and control their Zappi and Eddi power diversion products, supports fetching latest data, as well as changing charge modes, setting boost timers automatically from Octopus Agile pricing.

Includes the ability to run as a deamon to manage the system, automatically changing modes/priorities based on available power and other inputs, as well as control TP-LINK HS110 wifi power sockets, automaticaly querying the Nissan API for Leaf SOC data and displaying results to e-Paper display connected to a Raspberry Pi.  This includes a "charge to 80%" and "automatically charge if below 20% when connected" features.

This builds heavily on https://github.com/twonk/MyEnergi-App-Api and https://myenergi.info/api-f54/

# Configuration

The code can be run directly from a checkout, no install process is required however some non-core python libs might be needed.

A config file (~/.zappirc) is required and should contain at least the credentials used to authenticate against the MyEnergi severs,

```
username: <serial number of Hub>
password: <Password as used in App>
```

Additionally, to use the Leaf integration the you need the Nissan credentials, as well as the location of a local checkout of https://github.com/filcole/pycarwings2.git
```
leaf:
    username: <email@example.com>
    password: <Password for Nissan API>
    region: <NE>
pycarwings_path: </path/to/pycarwings2>
```

To use the Jaguar Land Rover integration the you need to pip install jlrpy and set config file with permissions. Config includes a max SOC - set to 100 if you wish to control max SOC from this application (https://github.com/ardevd/jlrpy.git)
```
jlr:
    username: <email@example.com>
    password: <Password for JLR Incontrol app>
```

For all cars it's possible to set the battery capacity and control the behaviour based on battery charge.  Setting the capacity is required to perform SOC calculations, the value here should be the amount of power requried to be delived to the car to change from 0% SOC to 100% SOC, as measured by the Zappi.
charge_below is the SOC value at which the Zappi will automatically change to ECO mode regardless of surplus, this allows for protection of battery against sitting at low SOC values and ensuring there's a minimum SOC available.
stop_at is the value that the Zappi will change into Stop mode, again to preserve battery.  charge_below and stop_at can both be set to 'null' to disable this feature.
```
leaf:
    capacity: 35
    charge_below: 20
    stop_at: 80
```

If using the Octopus Agile features it's necessary to set the region
```
agile:
    region: H
```

Finally, it's possible to apply some manual configuration to the CT clams on individual devices, for example if they are configured as 'Monitor' or 'AC Storage'.  In this way I am able to have mine report figures for my iBoost although it's seen by the Zappi as a AC Battery.

If you have a 3 phase Zappi and net across phases then you need to note this in the config as below or the system will assume non-netting and take grid values from phase 1 only. You can ignore this for single phase and non-netting 3phase.

For clarity on logging or display its optional to add a name for your Zappi here too
```
house_data:
    <Zaapi serial number>:
        ectt2: <Name to apply to this CT>
        name: Outside Zappi
    <Zappi serial number>:
        ectt3: <Name to apply to this CT>
        name: Garage Zappi
    net_phases1: True
```

# Use

Most scripts can be run directly, and will print results to screen, as well as logging to logs/myenergi.log

## get_zappi_boost.py
Shows the currently configured boost timers

## get_zappi_history.py
Shows the historic data from the cloud.  Can show both per minute data or per hour data for any specified day (if present on the servers) as well as fetching the data but reporting only daily totals.

## set_zappi_mode.py
Sets the current mode of the Zappi.  Currently sets the mode of all Zappis to stop, needs command line options added.

## set_boost_charge.py
Sets boost timers on the Zappi.  Automatically configures boost timers for the Zappi, based on Octopus Agile pricing.  Accepts options for desired SOC (Nissan Leaf only), desired time, charge rate, amount of charge and to clear existing timers.

## run_zappi.py
Reports current state of system to stdout, and can run as a server controlling the system.

## run_zappi.py start
Launches the deamon in the background.

__Will run as a server if run with no argunments and change Zappi mode automatically, use "run_zappi.py once" to simply display data and exit___

# API

The API is much wider than the utilities provided, and understands most of the known endpoints, including almost all of the functionality that exists in the App. It is antipiated that most people will want to interface directly with existing applications or monitoring systems rather than using the scripts directly.
