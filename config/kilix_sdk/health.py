"""Incident detection for optional system speech, independent of display units.

Temperature/RAM come from the shared telemetry sampler. Power and physical-link
state are small sysfs reads; no reachability probes or extra process sampler.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import time

ALERTS = {
    'overheat': 'WARNING: the system is overheating',
    'battery_low': 'battery low',
    'battery_critical': 'battery critically low',
    'offline': 'offline',
    'swap': 'heavy swapping',
}
OVERHEAT_C = 101.0


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def battery(root=Path('/')):
    """Return (combined percent, discharging), or None for missing readings."""
    packs = []
    for directory in (root/'sys/class/power_supply').glob('*'):
        if read(directory/'type') != 'Battery' or read(directory/'present') == '0':
            continue
        capacity = number(read(directory/'capacity'))
        now, full = number(read(directory/'energy_now')), number(read(directory/'energy_full'))
        if capacity is None and now is not None and full and full > 0:
            capacity = 100*now/full
        status = read(directory/'status')
        if capacity is None or not 0 <= capacity <= 100 or status not in ('Discharging', 'Charging', 'Full', 'Not charging'):
            return None
        packs.append((capacity, now, full, status.lower() == 'discharging'))
    if not packs:
        return None
    if all(now is not None and full is not None and full > 0 for _, now, full, _ in packs):
        percent = 100*sum(p[1] for p in packs)/sum(p[2] for p in packs)
    else:
        percent = sum(p[0] for p in packs)/len(packs)
    return min(100.0, max(0.0, percent)), any(p[3] for p in packs)


def link_online(root=Path('/')):
    """Any physical uplink has carrier; virtual/VPN/loopback do not mask loss."""
    directory = root/'sys/class/net'
    if not directory.is_dir():
        return None
    states = []
    try:
        interfaces = list(directory.iterdir())
    except OSError:
        return None
    for interface in interfaces:
        if interface.name == 'lo' or not (interface/'device').exists():
            continue
        carrier = read(interface/'carrier')
        state = read(interface/'operstate')
        states.append(True if carrier == '1' else False if carrier == '0' or state in ('down', 'lowerlayerdown', 'notpresent') else None)
    if True in states:
        return True
    return None if None in states else False


class AlertPolicy:
    """One announcement per incident, with recovery hysteresis and cooldowns."""
    def __init__(self, *, overheat_c=OVERHEAT_C):
        self.overheat_c = overheat_c
        self.active = set()
        self.last_spoken = {}
        self.battery_level = 0
        self.was_online = False
        self.offline_since = None
        self.swap_since = None
        self.previous_vm = None
        self.previous_time = None

    def _enter(self, key, now, events):
        if key not in self.active:
            self.active.add(key)
            cooldown = 60 if key == 'offline' else 300
            if now-self.last_spoken.get(key, -math.inf) >= cooldown:
                self.last_spoken[key] = now
                events.append(ALERTS[key])

    def update(self, *, now, temperature=None, power=None, online=None,
               memory=None, page_size=4096):
        events = []
        temperature = number(temperature)
        if temperature is not None:
            if temperature > self.overheat_c:
                self._enter('overheat', now, events)
            elif temperature <= self.overheat_c-3:
                self.active.discard('overheat')
        if power is not None:
            percent, discharging = power
            if not discharging or percent >= 12:
                self.active.difference_update(('battery_low', 'battery_critical'))
                self.battery_level = 0
            elif discharging:
                level = 2 if percent < 5 else 1 if percent < 10 else 0
                if level > self.battery_level:
                    self._enter('battery_critical' if level == 2 else 'battery_low', now, events)
                    self.battery_level = level
        if online is True:
            self.was_online = True
            self.offline_since = None
            self.active.discard('offline')
        elif online is False and self.was_online:
            if self.offline_since is None:
                self.offline_since = now
            elif now-self.offline_since >= 4:
                self._enter('offline', now, events)
        else:
            self.offline_since = None

        # Swap *usage* alone is not thrashing. Require scarce available RAM,
        # swap traffic, and measured memory stalls continuously for ten seconds.
        swapping = None
        if memory is not None:
            stamp, total, available, swap_total, counters, pressure = memory
            if self.previous_vm is not None and self.previous_time is not None:
                elapsed = stamp-self.previous_time
                deltas = [a-b for a, b in zip(counters, self.previous_vm)]
                if 0 < elapsed <= 5 and min(deltas) >= 0:
                    rate = sum(deltas)*page_size/elapsed
                    swapping = (total > 0 and available/total <= .02 and swap_total > 0
                                and rate >= 8*1024*1024 and pressure is not None and pressure >= 20)
            self.previous_vm, self.previous_time = counters, stamp
        else:
            self.previous_vm = self.previous_time = None
        if swapping is True:
            if self.swap_since is None:
                self.swap_since = now
            elif now-self.swap_since >= 10:
                self._enter('swap', now, events)
        else:
            self.swap_since = None
            if swapping is False:
                self.active.discard('swap')
        return events


class HealthMonitor:
    phrases = tuple(ALERTS.values())

    def __init__(self, *, client=None, root=Path('/'), clock=time.monotonic):
        if client is None:
            from .telemetry import TelemetryClient
            client = TelemetryClient()
        self.client, self.root, self.clock = client, Path(root), clock
        self.policy = AlertPolicy()
        self.next_start = 0.0
        self.last_stamp = None
        self.page_size = os.sysconf('SC_PAGE_SIZE')

    def poll(self):
        now = self.clock()
        start = now >= self.next_start
        if start:
            self.next_start = now+30
        try:
            snapshot = self.client.snapshot(start=start, fallback=False)
        except (OSError, RuntimeError):
            snapshot = None
        temperature = memory = None
        if snapshot is not None and 0 <= now-snapshot.monotonic_ns/1e9 <= 5:
            values = [number(sensor.celsius) for sensor in snapshot.thermal]
            temperature = max((v for v in values if v is not None), default=None)
            system = snapshot.system
            stamp = snapshot.monotonic_ns/1e9
            if stamp != self.last_stamp:
                vm = system.vm
                if 'pswpin' in vm and 'pswpout' in vm:
                    pressure = number(system.pressure.get('memory', {}).get('some_avg10'))
                    memory = (stamp, system.memory_total, system.memory_available,
                              system.swap_total, (vm['pswpin'], vm['pswpout']), pressure)
                self.last_stamp = stamp
        return self.policy.update(now=now, temperature=temperature,
            power=battery(self.root), online=link_online(self.root),
            memory=memory, page_size=self.page_size)

    def close(self):
        self.client.close()
