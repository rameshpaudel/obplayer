#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
Copyright 2012-2015 OpenBroadcaster, Inc.

This file is part of OpenBroadcaster Player.

OpenBroadcaster Player is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

OpenBroadcaster Player is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with OpenBroadcaster Player.  If not, see <http://www.gnu.org/licenses/>.
"""

import obplayer

import os
import time
import traceback
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, GstVideo


class ObLiveAssistMicrophone (object):
    def __init__(self, conn, mode, rate, encoding, blocksize):
        self.conn = conn
        self.mode = mode
        self.rate = rate
        self.encoding = encoding
        self.alaw_encode = True if self.encoding == 'a-law' else False
        self.blocksize = blocksize

        self.microphone_queue = [ ]
        self.monitor_queue = bytearray()
        self.lock = threading.Lock()
        self.pipeline = Gst.Pipeline()

        self.volume = None
        self.appsink = None
        self.appsrc = None

        if mode == 'mic' or mode == 'mic+monitor':
            self.add_microphone()
        if mode == 'monitor' or mode == 'mic+monitor':
            self.add_monitor()

    def add_microphone(self):
        elements = [ ]

        #elements.append(Gst.ElementFactory.make('autoaudiosrc', 'audiomixer-src'))
        self.appsrc = Gst.ElementFactory.make('appsrc', 'audiomixer-src')
        self.appsrc.set_property('is-live', True)
        self.appsrc.set_property('format', 3)
        self.appsrc.set_property('caps', Gst.Caps.from_string("audio/x-raw,channels=1,rate=" + str(self.rate) + ",format=S16LE,layout=interleaved"))
        self.appsrc.connect("need-data", self.cb_need_data)
        elements.append(self.appsrc)

        self.volume = Gst.ElementFactory.make('volume', 'audiomixer-volume')
        elements.append(self.volume)
        elements.append(Gst.ElementFactory.make('queue2'))

        ## create audio sink element
        audio_output = obplayer.Config.setting('live_assist_mic_mode')
        if audio_output == 'alsa':
            self.audiosink = Gst.ElementFactory.make('alsasink', 'audiosink')
            alsa_device = obplayer.Config.setting('live_assist_mic_alsa_device')
            if alsa_device != '':
                self.audiosink.set_property('device', alsa_device)

        elif audio_output == 'esd':
            self.audiosink = Gst.ElementFactory.make('esdsink', 'audiosink')

        elif audio_output == 'jack':
            self.audiosink = Gst.ElementFactory.make('jackaudiosink', 'audiosink')
            self.audiosink.set_property('connect', 0)  # don't autoconnect ports.
            name = obplayer.Config.setting('live_assist_mic_jack_name')
            self.audiosink.set_property('client-name', name if name else 'obplayer-liveassist-mic')

        elif audio_output == 'oss':
            self.audiosink = Gst.ElementFactory.make('osssink', 'audiosink')

        elif audio_output == 'pulse':
            self.audiosink = Gst.ElementFactory.make('pulsesink', 'audiosink')

        elif audio_output == 'test':
            self.audiosink = Gst.ElementFactory.make('fakesink', 'audiosink')

        else:
            self.audiosink = Gst.ElementFactory.make('autoaudiosink', 'audiosink')

        elements.append(self.audiosink)

        self.build_pipeline(elements)

    def add_monitor(self):
        elements = [ ]

        audio_input = obplayer.Config.setting('live_assist_monitor_mode')
        if audio_input == 'alsa':
            self.audiosrc = Gst.ElementFactory.make('alsasrc', 'audiosrc')
            alsa_device = obplayer.Config.setting('live_assist_monitor_alsa_device')
            if alsa_device != '':
                self.audiosrc.set_property('device', alsa_device)

        elif audio_input == 'jack':
            self.audiosrc = Gst.ElementFactory.make('jackaudiosrc', 'audiosrc')
            self.audiosrc.set_property('connect', 0)  # don't autoconnect ports.
            name = obplayer.Config.setting('live_assist_monitor_jack_name')
            self.audiosrc.set_property('client-name', name if name else 'obplayer-liveassist-monitor')

        elif audio_input == 'oss':
            self.audiosrc = Gst.ElementFactory.make('osssrc', 'audiosrc')

        elif audio_input == 'pulse':
            self.audiosrc = Gst.ElementFactory.make('pulsesrc', 'audiosrc')

        elif audio_input == 'test':
            self.audiosrc = Gst.ElementFactory.make('fakesrc', 'audiosrc')

        else:
            self.audiosrc = Gst.ElementFactory.make('autoaudiosrc', 'audiosrc')

        elements.append(self.audiosrc)
        elements.append(Gst.ElementFactory.make('queue2'))
        elements.append(Gst.ElementFactory.make('audioconvert'))
        elements.append(Gst.ElementFactory.make('audioresample'))

        self.appsink = Gst.ElementFactory.make("appsink", "appsink")
        self.appsink.set_property('emit-signals', True)
        self.appsink.set_property('caps', Gst.Caps.from_string("audio/x-raw,channels=1,rate=" + str(self.rate) + ",format=S16LE,layout=interleaved"))
        #self.appsink.set_property('blocksize', 4096)
        #self.appsink.set_property('max-buffers', 10)
        self.appsink.set_property('drop', True)
        self.appsink.set_property('max-lateness', 500000000)
        self.appsink.connect("new-sample", self.cb_new_sample)
        elements.append(self.appsink)

        self.build_pipeline(elements)

    def build_pipeline(self, elements):
        for element in elements:
            obplayer.Log.log("adding element to bin: " + element.get_name(), 'debug')
            self.pipeline.add(element)
        for index in range(0, len(elements) - 1):
            elements[index].link(elements[index + 1])

    def toggle_mute(self):
        if self.volume:
            mute = self.volume.get_property('mute')
            self.volume.set_property('mute', not mute)
            return self.volume.get_property('mute')

    def change_volume(self, volume):
        if self.volume:
            self.volume.set_property('volume', float(volume) / 100.0)

    def get_volume(self):
        if self.volume:
            return {
                'volume': self.volume.get_property('volume') * 100,
                'mute': self.volume.get_property('mute')
            }

    def start(self):
        self.wait_state(Gst.State.PLAYING)

    def stop(self):
        self.wait_state(Gst.State.NULL)

    def quit(self):
        self.wait_state(Gst.State.NULL)

    def wait_state(self, target_state):
        self.pipeline.set_state(target_state)
        (statechange, state, pending) = self.pipeline.get_state(timeout=5 * Gst.SECOND)
        if statechange != Gst.StateChangeReturn.SUCCESS:
            obplayer.Log.log("gstreamer failed waiting for state change to " + str(pending), 'error')
            #raise Exception("Failed waiting for state change")
            return False
        return True

    def change_format(self, rate, encoding):
        self.rate = rate
        self.appsink.set_property('caps', Gst.Caps.from_string("audio/x-raw,channels=1,rate=" + str(self.rate) + ",format=S16LE,layout=interleaved"))
        self.appsrc.set_property('caps', Gst.Caps.from_string("audio/x-raw,channels=1,rate=" + str(self.rate) + ",format=S16LE,layout=interleaved"))

    def queue_data(self, data):
        with self.lock:
            self.microphone_queue.append(data)

    def cb_need_data(self, unused, userdata):
        with self.lock:
            if len(self.microphone_queue):
                data = self.microphone_queue.pop(0)
            else:
                data = bytearray(self.blocksize)
        gbuffer = Gst.Buffer.new_allocate(None, len(data), None)
        gbuffer.fill(0, data)
        ret = self.appsrc.emit('push-buffer', gbuffer)

    def cb_new_sample(self, userdata):
        gbuffer = self.appsink.get_property('last-sample').get_buffer()
        data = gbuffer.extract_dup(0, gbuffer.get_size())
        if self.alaw_encode:
            data = self.encodeAlawBuffer(data)
        self.monitor_queue += data

        while len(self.monitor_queue) >= self.blocksize:
            data = self.monitor_queue[:self.blocksize]
            self.monitor_queue = self.monitor_queue[self.blocksize:]
            #obplayer.Log.log("websocket send: " + str(len(data)) + " " + repr(data[:20]) + "...", 'debug')
            if self.conn:
                self.conn.websocket_write_message(obplayer.httpadmin.httpserver.WS_OP_BIN, data)
        return Gst.FlowReturn.OK

    """
    def pull_buffer(self):
        sample = self.appsink.emit('pull-sample')
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0, buffer.get_size())
        return data
    """

    def encodeAlawBuffer(self, data):
        output = bytearray(len(data) / 2)
        for i in range(0, len(data), 2):
            sample = (ord(data[i]) << 8) | ord(data[i + 1])
            sign = (~ord(data[i])) & 0x80;
            if not sign:
                sample = ~sample;
            if sample > 32635:
                sample = 32635;
            if sample >= 256:
                exponent = AlawEncodeTable[ord(data[i]) & 0x7F];
                mantissa = (sample >> (exponent + 3) ) & 0x0F;
                alawsample = ((exponent << 4) | mantissa);
            else:
                alawsample = (sample >> 4) & 0xff;

            alawsample ^= (sign ^ 0x55)
            output[int(i / 2)] = alawsample
        return output

AlawEncodeTable = [
     1,1,2,2,3,3,3,3,
     4,4,4,4,4,4,4,4,
     5,5,5,5,5,5,5,5,
     5,5,5,5,5,5,5,5,
     6,6,6,6,6,6,6,6,
     6,6,6,6,6,6,6,6,
     6,6,6,6,6,6,6,6,
     6,6,6,6,6,6,6,6,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7,
     7,7,7,7,7,7,7,7
]

