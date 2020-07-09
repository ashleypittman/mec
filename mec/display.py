#!/usr/bin/python3

import sys
import logging

try:
    from PIL import Image,ImageDraw,ImageFont
except ModuleNotFoundError:
    pass

FONT_FILE = '/home/pi/bitmap-fonts/bitmap/terminus-font-4.39/ter-u14v.bdf'

log = logging.getLogger('e-paper')

class ePaper():

    def __init__(self, conf):
        if 'waveshare_path' in conf:
             sys.path.append(conf['waveshare_path'])
        try:
            self._wave = __import__('waveshare_epd.epd2in7')
        except ModuleNotFoundError:
            self._wave = None
            return
        self.font_size = 14
        self.font = ImageFont.truetype(FONT_FILE, self.font_size)
        self._showing = None
        self._to_show = None

    def sample(self, conn, sessions, culm_values, sockets):

        if not self._wave:
            return
        state = conn.state
        text = []
        for zappi in state.zappi_list():
            if zappi.car_connected():
                car = 'Unknown'
                if sessions[zappi.sno]['se'].session._is_leaf:
                    session = sessions[zappi.sno]['se'].session
                    car = 'Leaf ({:.0f}%)'.format(session.percent_charge())
                elif sessions[zappi.sno]['se'].session._is_leaf is False:
                    car = 'Outlander'
                if zappi.status == 'Hot':
                    text.append('Fully charged {}kWh {}'.format(zappi.charge_added, car))
                else:
                    div = zappi.charge_rate
                    if div:
                        text.append('{} {:.1f}kW {}kWh {}'.format(zappi.mode, div/1000,
                                                               zappi.charge_added,
                                                               car))
                    elif zappi.mode == 'Stop':
                        text.append('{} {}'.format(zappi.mode, car))
                    else:
                        text.append('{} {}'.format(zappi.status, car))
            else:
                text.append('No car connected')
        ival = state._values['Grid'] / 1000
        gval = state._values['Generation'] / 1000
        if abs(gval) < 0.2:
            gen = 'No Generation'
        else:
            gen = 'Generation {:.1f}'.format(gval)
        if abs(ival) < 0.1:
            istate = 'No import/export'
        elif ival >= 0:
            istate = 'Import {:.1f}'.format(ival)
        else:
            istate = 'Export {:.1f}'.format(-ival)
        grid_today = culm_values['Grid']
        text.append('{} {}'.format(gen, istate))
        text.append('Days import/export: {:.1f} {:.1f}'.format(grid_today.kwh(), grid_today.nkwh()))
        iboost_today = culm_values['iBoost']
        if state._values['iBoost'] < 50:
            text.append('Water is not heating {:.1f}kWh today'.format(iboost_today.kwh()))
        else:
            text.append('Water {:.1f}kw {:.1f}kWh today'.format(state._values['iBoost'] / 1000,
                                                                         iboost_today.kwh()))
        text.append('')
        for socket in sockets:
            if socket.name != 'Dehumidifier':
                continue
            if socket.on:
                s_state = 'on'
            else:
                s_state = 'off'
            if socket._history.is_satisfied(runtime=5*60, power=10):
                text.append('{} is satisfied'.format(socket.name))
            else:
                text.append('{} {}, {:.1f} today'.format(socket.name, s_state, socket.todays_kwh()))
        heating_value = state._values['Heating']
        if abs(heating_value) < 20:
            text.append('Heating is off')
        else:
            text.append('Heating is on')
        self._to_show = text
        log.debug(self._showing)
        log.debug(self._to_show)

    def update(self):

        if not self._wave:
            return

        if self._to_show == self._showing:
            log.debug("Data hasn't changed, not updating screen")
            return

        epd = self._wave.epd2in7.EPD()
        epd.init()

        line_gap = 2
        top_gap = 1

        page = Image.new('1', (epd.height, epd.width), 255)
        draw = ImageDraw.Draw(page)
        lines = ['Connected, waiting...', 
                'Generation 2kw, export 1kw',
                'I: 12 E: 12 C: 12 HW: 12',
                'State of charge: 80%, plenty o',
                'Water heating, total 12kWh',
                'Dehumidifier on'
                'time']

        l = 0
        for line in self._to_show:
            draw.text((top_gap,
                      (l* (self.font_size + line_gap)) + top_gap),
                      line, font = self.font, fill = 0)
            l += 1
        epd.display(epd.getbuffer(page))
        self._showing = self._to_show 
        epd.sleep()
