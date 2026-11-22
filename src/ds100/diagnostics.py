"""Bounded local measurements. Collection adds no USB requests or file writes."""
# SPDX-License-Identifier: GPL-3.0-only
from collections import Counter, deque
from threading import Lock
import struct
import time


STAT_FIELDS = ('elapsed_ms', 'epochs', 'early_attempts', 'early_drains',
               'waits', 'wait_ms', 'deadlines', 'overflow_faults',
               'geometry_faults', 'peak_queue_bytes', 'queue_capacity',
               'last_fault', 'lease_age_ms')


def classify(error):
    text = str(error).lower()
    if 'crc' in text or 'checksum' in text: return 'crc'
    if 'sequence' in text or 'identifier' in text: return 'sequence'
    if 'geometry' in text or 'coordinate' in text: return 'geometry'
    if 'overflow' in text: return 'queue_overflow'
    if 'timeout' in text or 'timed out' in text: return 'transport_timeout'
    if 'inactive' in text: return 'inactive'
    if 'new baseline' in text: return 'baseline_required'
    return type(error).__name__


class Diagnostics:
    def __init__(self):
        self._lock = Lock()
        self.requests = self.received_bytes = 0
        self.errors = Counter()
        self.latencies = deque(maxlen=512)
        self.resync_events = deque(maxlen=32)
        self.stop_reports = deque(maxlen=16)
        self.sync_intervals = deque(maxlen=32)
        self._sync_started = None

    def request(self, elapsed, received, error=None):
        with self._lock:
            self.requests += 1
            self.received_bytes += received
            self.latencies.append(elapsed)
            if error is not None: self.errors[classify(error)] += 1

    def resync(self, error):
        with self._lock:
            self.resync_events.append(dict(monotonic=time.monotonic(), reason=classify(error), text=str(error)))

    def sync(self, active):
        with self._lock:
            if active:
                self._sync_started = time.monotonic()
            elif self._sync_started is not None:
                self.sync_intervals.append(time.monotonic() - self._sync_started)
                self._sync_started = None

    def stop(self, payload):
        if not payload: return
        if len(payload) != 64: raise ValueError('The STS1 reply length is invalid.')
        words = struct.unpack('<16I', payload)
        if words[:2] != (0x31535453, 1) or words[-1]:
            raise ValueError('The STS1 reply header is invalid.')
        report = dict(zip(STAT_FIELDS, words[2:15]))
        if report['queue_capacity'] not in (4096, 8192, 16384):
            raise ValueError('The STS1 queue capacity is invalid.')
        with self._lock:
            self.stop_reports.append(report)

    def snapshot(self):
        with self._lock:
            ordered = sorted(self.latencies)
            report = dict(requests=self.requests, received_bytes=self.received_bytes,
                errors=dict(self.errors), resync_events=list(self.resync_events),
                sync_intervals_seconds=list(self.sync_intervals), stop_reports=list(self.stop_reports))
        percentile = lambda p: ordered[min(len(ordered)-1, int((len(ordered)-1)*p))]*1000 if ordered else None
        report.update(latency_p50_ms=percentile(.5), latency_p95_ms=percentile(.95), latency_samples=len(ordered))
        return report
