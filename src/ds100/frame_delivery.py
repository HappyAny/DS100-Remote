"""One owned frame and one Qt wake notification per connection generation."""
# SPDX-License-Identifier: GPL-3.0-only
from threading import Lock
import numpy as np


class LatestFrame:
    def __init__(self):
        self._lock = Lock()
        self.generation = 0
        self._pending = None
        self._notified = False
        self.received = self.replaced = self.consumed = self.rejected = 0

    def reset(self):
        with self._lock:
            self.generation += 1
            self._pending = None
            self._notified = False
            return self.generation

    def publish(self, pixels, elapsed, generation, preview=False):
        with self._lock:
            if generation != self.generation:
                self.rejected += 1
                return False
        owned = np.array(pixels, dtype='<u2', order='C', copy=True)
        if owned.shape != (320, 480):
            raise ValueError('The display frame must contain 480 by 320 pixels.')
        owned.flags.writeable = False
        with self._lock:
            if generation != self.generation:
                self.rejected += 1
                return False
            self.received += 1
            if self._pending is not None:
                self.replaced += 1
            self._pending = (owned, elapsed, preview)
            notify = not self._notified
            self._notified = True
            return notify

    def take(self, generation):
        with self._lock:
            if generation != self.generation:
                return None
            frame, self._pending = self._pending, None
            self._notified = False
            if frame is not None:
                self.consumed += 1
            return frame

    def snapshot(self):
        with self._lock:
            return dict(received=self.received, replaced=self.replaced,
                        consumed=self.consumed, rejected=self.rejected,
                        pending=int(self._pending is not None), generation=self.generation)
