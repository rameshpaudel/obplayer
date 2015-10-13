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
import sys
import time
import traceback

import json

import OpenSSL

if sys.version.startswith('3'):
    import socketserver as SocketServer
    import http.server as BaseHTTPServer
else:
    import SocketServer
    import BaseHTTPServer

from obplayer.httpadmin import httpserver

from . import microphone


class ObLiveAssist(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    daemon_threads = True

    def __init__(self):
        self.root = 'obplayer/liveassist/http'
        self.username = None
        self.readonly_username = None
        self.websockets = [ ]

        server_address = ('', obplayer.Config.setting('live_assist_port'))  # (address, port)

        BaseHTTPServer.HTTPServer.__init__(self, server_address, httpserver.ObHTTPRequestHandler)
        sa = self.socket.getsockname()
        self.log('serving live assist http on port ' + str(sa[1]))

    def log(self, message):
        obplayer.Log.log(message, 'debug')

    def shutdown(self):
        for conn in self.websockets:
            conn.websocket_write_close(200, "Server Exiting")
        BaseHTTPServer.HTTPServer.shutdown(self)

    def handle_post(self, path, postvars, access):
        #if not access:
        #    return { 'status' : False, 'error' : "You don't have permission to do that.  You are current logged in as a guest" }

        if path == '/info/current_time':
            return { 'value' : str(time.time()) }

        elif path == '/info/show_name':
            return { 'value' : obplayer.Scheduler.get_show_name() }

        elif path == '/info/show_end':
            return { 'value' : str(obplayer.Scheduler.get_show_end()) }

        elif path == '/info/playlist':
            playlist = obplayer.Scheduler.get_current_playlist()
            return playlist

        elif path == '/info/liveassist_groups':
            groups = obplayer.Scheduler.get_current_groups()
            return groups

        elif path == '/info/play_status':
            return obplayer.Scheduler.get_now_playing()

        elif path == '/command/play':
            if obplayer.Scheduler.unpause_show() == True:
                return '{"status": True}'
            return { 'status' : False }

        elif path == '/command/pause':
            if obplayer.Scheduler.pause_show() == True:
                return '{"status": True}'
            return { 'status' : False }

        elif path == '/command/play_group_item':
            try:
                group_num = int(postvars['group_num'][0])
                group_item_num = int(postvars['group_item_num'][0])
                position = float(postvars['position'][0])
            except AttributeError as e:
                return { 'status' : False, 'error': "invalid request, missing " + e.args[0] + "." }

            if obplayer.Scheduler.play_group_item(group_num, group_item_num, position):
                return { 'status' : True }
            else:
                return { 'status' : False }

        elif path == '/command/playlist_seek':
            try:
                track_num = int(postvars['track_num'][0])
                position = float(postvars['position'][0])
            except AttributeError as e:
                return { 'status' : False, 'error': "invalid request, missing " + e.args[0] + "." }

            if obplayer.Scheduler.playlist_seek(track_num, position):
                return { 'status' : True }
            else:
                return { 'status' : False }


    def handle_websocket(self, conn, path):
        if path != '/stream':
            return

        self.websockets.append(conn)
        conn.microphone = None
        while True:
            try:
                (opcode, msg) = conn.websocket_read_message()
                if not msg:
                    break

                if opcode == httpserver.WS_OP_TEXT:
                    msg = json.loads(msg)
                    if msg['type'] == 'negotiate':
                        print(repr(msg))
                        if not conn.microphone:
                            conn.microphone = microphone.ObLiveAssistMicrophone(conn, msg['mode'], msg['rate'], msg['encoding'], msg['blocksize'])
                            conn.microphone.start()
                            self.send_mic_status(conn)
                        else:
                            pass #conn.microphone.change_format(msg['rate'], msg['encoding'])

                    elif msg['type'] == 'mute':
                        if conn.microphone:
                            conn.microphone.toggle_mute()
                        self.send_mic_status(conn)

                    elif msg['type'] == 'volume':
                        if conn.microphone:
                            conn.microphone.change_volume(msg['volume'])
                        self.send_mic_status(conn)

                elif opcode == httpserver.WS_OP_BIN:
                    #obplayer.Log.log("websocket recv: " + str(len(msg)) + " " + repr(msg[:20]) + "...", 'debug')
                    #conn.websocket_write_message(httpserver.WS_OP_BIN, ''.join(chr(random.getrandbits(8)) for i in range(len(msg))))
                    #conn.websocket_write_message(opcode, msg)
                    if not conn.microphone:
                        obplayer.Log.log("websocket audio stream not negotiated", 'error')
                    elif type(msg) == bytearray:
                        conn.microphone.queue_data(msg)
                    #conn.websocket_write_message(httpserver.WS_OP_BIN, data)

            except OSError as e:
                obplayer.Log.log("OSError: " + str(e), 'error')
                break

            except httpserver.WebSocketError as e:
                obplayer.Log.log(str(e), 'error')
                break

            except Exception as e:
                obplayer.Log.log(traceback.format_exc(), 'error')

        obplayer.Log.log("websocket connection closed", 'liveassist')
        if conn.microphone:
            conn.microphone.quit()
        self.websockets.remove(conn)

    def send_mic_status(self, conn):
        if not conn.microphone:
            return
        msg = conn.microphone.get_volume()
        if not msg:
            return
        msg['type'] = 'mic-status'
        conn.websocket_write_message(httpserver.WS_OP_TEXT, json.dumps(msg))

