#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author:: Gilles Devaux <gilles.devaux@gmail.com>
# Based on https://github.com/ganglia/gmond_python_modules/blob/master/memcached/python_modules/memcached.py
# but using 'positive' slope to create COUNTER rrds for rates, no more _rate keys. Also removed tokyo tyrant
# requires ganglia 3.1

import sys
import traceback
import os
import threading
import time
import socket
import select

descriptors = list()
desc_Skel = {}
_Worker_Thread = None
_Lock = threading.Lock() # synchronization lock
debug = False

def dprint(f, *v):
    if debug:
        print >> sys.stderr, "DEBUG: " + f % v


def floatable(str):
    try:
        float(str)
        return True
    except Exception:
        return False


class UpdateMetricThread(threading.Thread):
    """
    This is so we do not open/close connections for STAT all the time
    """

    def __init__(self, params):
        threading.Thread.__init__(self)
        self.running = False
        self.shuttingdown = False
        self.refresh_rate = 15
        if "refresh_rate" in params:
            self.refresh_rate = int(params["refresh_rate"])
        self.metric = {}
        self.timeout = 2

        self.host = "localhost"
        self.port = 11211
        if "host" in params:
            self.host = params["host"]
        if "port" in params:
            self.port = int(params["port"])
        self.mp = params["metrix_prefix"]

    def shutdown(self):
        self.shuttingdown = True
        if not self.running:
            return
        self.join()

    def run(self):
        self.running = True

        while not self.shuttingdown:
            _Lock.acquire()
            self.update_metric()
            _Lock.release()
            time.sleep(self.refresh_rate)

        self.running = False

    def update_metric(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        msg = ""
        try:
            dprint("connect %s:%d", self.host, self.port)
            sock.connect((self.host, self.port))
            sock.send("stats\r\n")

            while True:
                rfd, wfd, xfd = select.select([sock], [], [], self.timeout)
                if not rfd:
                    print >> sys.stderr, "ERROR: select timeout"
                    break

                for fd in rfd:
                    if fd == sock:
                        data = fd.recv(8192)
                        msg += data

                if msg.find("END"):
                    break

            sock.close()
        except socket.error, e:
            print >> sys.stderr, "ERROR: %s" % e

        for m in msg.split("\r\n"):
            d = m.split(" ")
            if len(d) == 3 and d[0] == "STAT" and floatable(d[2]):
                self.metric[self.mp + "_" + d[1]] = float(d[2])

    def metric_of(self, name):
        _Lock.acquire()
        val = self.metric[name]
        _Lock.release()
        return val


def metric_init(params):
    global descriptors, desc_Skel, _Worker_Thread, debug

    print '[memcached] memcached protocol "stats"'

    if "metrix_prefix" not in params:
        params["metrix_prefix"] = "mc"

    print params

    # initialize skeleton of descriptors
    desc_Skel = {
        'name': 'XXX',
        'call_back': metric_of,
        'time_max': 60,
        'value_type': 'float',
        'format': '%.0f',
        'units': 'XXX',
        'slope': 'XXX', # zero|positive|negative|both
        'description': 'XXX',
        'groups': 'memcache',
        }

    if "refresh_rate" not in params:
        params["refresh_rate"] = 15
    if "debug" in params:
        debug = params["debug"]
    dprint("%s", "Debug mode on")

    _Worker_Thread = UpdateMetricThread(params)
    _Worker_Thread.start()

    # IP:HOSTNAME
    if "spoof_host" in params:
        desc_Skel["spoof_host"] = params["spoof_host"]

    mp = params["metrix_prefix"]

    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_curr_items",
        "units": "items",
        "slope": "both",
        "description": "Current number of items",
        }))
    for cmd in ['get', 'set', 'flush']:
        descriptors.append(create_desc(desc_Skel, {
            "name": mp + "_cmd_%s" % cmd,
            "units": "commands",
            "slope": "positive",
            "description": "%s reqs" % cmd,
            }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_bytes_read",
        "units": "bytes",
        "slope": "positive",
        "description": "Bytes read",
        }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_bytes_written",
        "units": "bytes",
        "slope": "positive",
        "description": "Bytes written",
        }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_bytes",
        "units": "bytes",
        "slope": "both",
        "description": "Current size",
        }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_limit_maxbytes",
        "units": "bytes",
        "slope": "both",
        "description": "Max size",
        }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_curr_connections",
        "units": "connections",
        "slope": "both",
        "description": "Connections",
        }))
    descriptors.append(create_desc(desc_Skel, {
        "name": mp + "_evictions",
        "units": "items",
        "slope": "both",
        "description": "Evictions",
        }))
    for cmd in ['get', 'delete', 'incr', 'decr', 'cas']:
        descriptors.append(create_desc(desc_Skel, {
            "name": mp + "_%s_hits" % cmd,
            "units": "items",
            "slope": "positive",
            "description": "%s hits" % cmd,
            }))
        descriptors.append(create_desc(desc_Skel, {
            "name": mp + "_%s_misses" % cmd,
            "units": "items",
            "slope": "positive",
            "description": "%s miss" % cmd,
            }))

    return descriptors


def create_desc(skel, prop):
    d = skel.copy()
    for k, v in prop.iteritems():
        d[k] = v
    return d


def metric_of(name):
    return _Worker_Thread.metric_of(name)


def metric_cleanup():
    _Worker_Thread.shutdown()

if __name__ == '__main__':
    Debug = True
    try:
        params = {
            "host": "localhost",
            "port": 11211,
            # "host"  : "tt101",
            # "port"  : 1978,
            # "metrix_prefix" : "tt101",
            "debug": True,
            }
        metric_init(params)

        while True:
            for d in descriptors:
                v = d['call_back'](d['name'])
                print ('value for %s is ' + d['format']) % (d['name'], v)
            time.sleep(5)
    except KeyboardInterrupt:
        time.sleep(0.2)
        os._exit(1)
    except Exception:
        traceback.print_exc()
        os._exit(1)
