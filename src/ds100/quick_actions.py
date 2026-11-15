"""Named device actions with acknowledged and paced native input steps."""

from dataclasses import dataclass
import time
from .protocol import ProtocolError


@dataclass(frozen=True)
class QuickAction:
    title: str
    hint: str
    # Each step is key mask, wheel I, wheel II, delay after acknowledgement.
    steps: tuple


def tap(mask):
    return ((mask, 0, 0, .08), (0, 0, 0, .08))


def turn(mask, wheel, direction):
    return ((mask, direction if wheel == 0 else 0, direction if wheel == 1 else 0, .08),
            (0, 0, 0, .08))


def double_tap(mask):
    return ((mask, 0, 0, .08), (0, 0, 0, .10), (mask, 0, 0, .08), (0, 0, 0, .08))


ACTIONS = {
    'auto': QuickAction('自动找波形', '原机 AUTO：重新选择合适的采集参数，可能需要数秒。', tap(2)),
    'run_pause': QuickAction('运行 / 暂停', '切换当前运行状态；不假定设备现在处于哪种状态。', tap(1)),
    'channel': QuickAction('切换通道', '原机 E 双击，切换当前选中通道。',
                           double_tap(8)),
    'center_exit': QuickAction('退出 / 回中', '菜单中退出；波形界面按原机 E 键规则回中。', tap(8)),
    'time_down': QuickAction('时基 −', 'E + 拨轮 I，减少一档时基。', turn(8, 0, -1)),
    'time_up': QuickAction('时基 +', 'E + 拨轮 I，增加一档时基。', turn(8, 0, 1)),
    'range_down': QuickAction('量程 −', 'E + 拨轮 II，减少当前通道的一档垂直量程。', turn(8, 1, -1)),
    'range_up': QuickAction('量程 +', 'E + 拨轮 II，增加当前通道的一档垂直量程。', turn(8, 1, 1)),
    'left': QuickAction('水平 ←', '波形向屏幕左侧移动；可按住连续移动。', turn(4, 0, 1)),
    'right': QuickAction('水平 →', '波形向屏幕右侧移动；可按住连续移动。', turn(4, 0, -1)),
    'down': QuickAction('垂直 ↓', 'M + 拨轮 II，垂直位置负向一步。', turn(4, 1, -1)),
    'up': QuickAction('垂直 ↑', 'M + 拨轮 II，垂直位置正向一步。', turn(4, 1, 1)),
    'double_p': QuickAction('P 双击', '发送两次 P 短按，原机分别处理。', double_tap(1)),
    'double_a': QuickAction('A 双击', '发送两次 A 短按，自动设置可能分别需要数秒。', double_tap(2)),
    'double_m': QuickAction('M 双击', '发送两次 M 短按，原机分别处理。', double_tap(4)),
    'double_e': QuickAction('E 双击', '原机 E 双击，切换当前选中通道。', double_tap(8)),
}

DEFAULT_FAVORITES = ('auto', 'run_pause', 'channel', 'center_exit')
MOTION_ACTIONS = frozenset(('left', 'right', 'down', 'up'))


def execute_motion(client, action_id, stopped, cancelled=lambda: False, on_frame=None,
                   clock=time.monotonic, wait=time.sleep):
    """Keep the modifier down for the gesture; release on every exit path."""
    if action_id not in MOTION_ACTIONS:
        raise ValueError('The continuous motion is not supported.')
    mask, wheel1, wheel2, _ = ACTIONS[action_id].steps[0]
    previous = client.modifier_gesture
    client.modifier_gesture = client.fast_modifiers
    started = clock()
    next_repeat = started + .25
    active = False
    detents = 0
    adopted = None
    try:
        if cancelled():
            raise ProtocolError('The quick action was cancelled.')
        active = True
        client.queue_input(mask, wheel1, wheel2)
        client.deliver_inputs(cancelled)
        detents += 1
        adopted = clock()
        while True:
            if cancelled():
                raise ProtocolError('The quick action was cancelled.')
            now = clock()
            # A fast click still submits one detent and an 80 ms key press.
            if stopped.is_set() and now - started >= .08:
                break
            if not stopped.is_set() and now >= next_repeat:
                client.queue_input(mask, wheel1, wheel2)
                client.deliver_inputs(cancelled)
                detents += 1
                next_repeat = clock() + .04
            session = client.draw_session
            occupied = False
            if session is not None and session.replay is None:
                before = clock()
                pixels = client.screen(cancelled)
                if pixels is not None and on_frame is not None:
                    on_frame(pixels, clock()-before)
                occupied = True
            if (session is not None and session.replay is not None and session.replay.ready and
                    client.screen_mode == 'draw-stream'):
                before = clock()
                pixels, occupied = session.poll_active()
                if pixels is not None and on_frame is not None:
                    on_frame(pixels, clock() - before)
            if not occupied:
                wait(.001)
    finally:
        if active:
            try:
                client.queue_input(0)
                client.flush_inputs()
            except (OSError, ProtocolError):
                pass
        client.modifier_gesture = previous
        client.last_motion = dict(action=action_id, seconds=clock()-started, detents=detents,
                                  initial_delivery_seconds=None if adopted is None else adopted-started,
                                  stopped=stopped.is_set())
    return action_id


def execute_action(client, action_id, cancelled=lambda: False, wait=time.sleep, on_frame=None):
    """Run in the USB worker. Wait after each ack; never bulk-queue a gesture."""
    if action_id not in ACTIONS:
        raise ValueError('The quick action is not supported.')
    active = 0
    previous_modifier = client.modifier_gesture
    if action_id in ('left', 'right', 'down', 'up') and client.fast_modifiers:
        client.modifier_gesture = True
    try:
        for mask, wheel1, wheel2, duration in ACTIONS[action_id].steps:
            if cancelled():
                raise ProtocolError('The quick action was cancelled.')
            active = mask
            client.queue_input(mask, wheel1, wheel2)
            client.deliver_inputs(cancelled)
            # The ack confirms receipt. Leave time for the native GUI to
            # consume the key before a wheel event or the next key edge.
            remaining = duration
            while remaining > .000001:
                if cancelled():
                    raise ProtocolError('The quick action was cancelled.')
                started = time.monotonic()
                occupied = False
                session = client.draw_session
                if (session is not None and session.replay is not None and session.replay.ready and
                        client.screen_mode == 'draw-stream'):
                    pixels, occupied = session.poll_active()
                    if pixels is not None and on_frame is not None:
                        on_frame(pixels, time.monotonic() - started)
                    remaining -= time.monotonic() - started
                streaming = session is not None and session.replay is not None and session.replay.ready
                interval = min(0 if occupied else .001 if streaming else .02, max(0, remaining))
                wait(interval)
                remaining -= interval
    finally:
        if active:
            # Also attempt release when a request failed after reaching the
            # device. Firmware lease expiry is the fallback if USB is lost.
            try:
                client.queue_input(0)
                client.flush_inputs()
            except (OSError, ProtocolError):
                pass
        client.modifier_gesture = previous_modifier
    return action_id
