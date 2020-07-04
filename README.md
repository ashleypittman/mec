Python client and API for monitoring and controling energy diversion devices from [MyEnergi](https://myenergi.com/)

A set of library functions and objects for interfacing with the cloud servers of MyEnergi to monitor and control their Zappi and Eddi power diversion products, supports fetching latest data, as well as changing charge modes, setting boost timers automatically from Octopus Agile pricing.

Includes the ability to run as a deamon to manage the system, automatically changing modes/priorities based on available power and other inputs, as well as control TP-LINK HS110 wifi power sockets, automaticaly querying the Nissan API for Leaf SOC data and displaying results to e-Paper display connected to a Raspberry Pi.  This includes a "charge to 80%" and "automatically charge if below 20% when connected" features.

This builds heavily on https://github.com/twonk/MyEnergi-App-Api and https://myenergi.info/api-f54/

# Configuration

The code can be run directly from a checkout, no install process is required however some non-core python libs might be needed.

A config file is required and should contain at least the credentials used to authenticate against the MyEnergi severs,

```
username: <serial number of Hub>
password: <Password as used in App>
```

Additionally, to use the Leaf integrarion the you need the Nissan credentials, as well as the location of a local checkout of https://github.com/filcole/pycarwings2.git
```
leaf:
    username: <email@example.com>
    password: <Password for Nissan API>
    region: <NE>
pycarwings_path: </path/to/pycarwings2>
```

If using the Octopus Agile features it's necessary to set the region
```
agile:
    region: H
```

Finally, it's possible to apply some manual configuration to the CT clams on individual devices, for example if they are configured as 'Monitor' or 'AC Storage'.  In this way I am able to have mine report figures for my iBoost although it's seen by the Zappi as a AC Battery.
```
house_data:
    <Zaapi serial number>:
        ectt2: <Name to apply to this CT>
    <Zappi serial number>:
        ectt3: <Name to apply to this CT>
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

__Will run as a server if run with no argunments and change Zappi mode automatically, use "run_zappi.py once" to simply display data and exit___

# API

The API is much wider than the utilities provided, and understands most of the known endpoints, including almost all of the functionality that exists in the App. It is antipiated that most people will want to interface directly with existing applications or monitoring systems rather than using the scripts directly.
