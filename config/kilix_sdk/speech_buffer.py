"""Bounded load-aware timing estimates for sequential speech producers.

The caller supplies positive work units and a nonnegative load score. Persist
state() privately to learn across runs. This module owns no files, devices,
threads or model; it only decides when queued audio needs replenishment.
"""
import math


class AdaptiveBuffer:
    VERSION = 1
    MAX_SAMPLES = 64

    def __init__(self, state=None, buffer_seconds=8.0):
        if not math.isfinite(buffer_seconds) or buffer_seconds < 0:
            raise ValueError('buffer_seconds must be finite and nonnegative')
        self.buffer_seconds = buffer_seconds
        self.samples = []
        if state and state.get('version') == self.VERSION:
            for row in state.get('samples', [])[-self.MAX_SAMPLES:]:
                try:
                    self.observe(row['work'], row['elapsed'], row['load'])
                except (KeyError, TypeError, ValueError):
                    continue

    def observe(self, work, elapsed, load):
        if not all(math.isfinite(n) for n in (work, elapsed, load)) or work <= 0 or elapsed <= 0 or load < 0:
            raise ValueError('work and elapsed must be positive; load must be nonnegative')
        self.samples.append({'work': work, 'elapsed': elapsed, 'load': load})
        del self.samples[:-self.MAX_SAMPLES]

    def state(self):
        return {'version': self.VERSION, 'samples': [dict(row) for row in self.samples]}

    def estimate(self, work, load):
        if not math.isfinite(work) or work <= 0 or not math.isfinite(load) or load < 0:
            raise ValueError('invalid work or load')
        if not self.samples:
            mean, spread, extrapolation = 1.0, 0.15, 1 + min(load, 2) * 0.25
        else:
            weighted = []
            for age, row in enumerate(reversed(self.samples)):
                weight = 0.96 ** age / (1 + 8 * abs(load - row['load'])) ** 2
                weighted.append((weight, row['elapsed'] / row['work']))
            total = sum(weight for weight, _ in weighted)
            mean = sum(weight * value for weight, value in weighted) / total
            spread = math.sqrt(sum(weight * (value - mean) ** 2 for weight, value in weighted) / total)
            # History cannot guarantee a deadline against an arbitrary stall.
            extrapolation = 1 + min(2, max(0, load - max(r['load'] for r in self.samples))) * 0.5
        average = work * mean * extrapolation
        safety = self.buffer_seconds + work * spread * 2 * extrapolation
        return {'average_seconds': average, 'safety_seconds': safety,
                'lead_seconds': average + safety, 'samples': len(self.samples)}

    def delay(self, queued_audio_seconds, work, load):
        if not math.isfinite(queued_audio_seconds) or queued_audio_seconds < 0:
            raise ValueError('queued audio must be finite and nonnegative')
        return max(0.0, queued_audio_seconds - self.estimate(work, load)['lead_seconds'])
