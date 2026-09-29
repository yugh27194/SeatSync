"""현재 시각(epoch 초). time.time() 직접 호출은 여기서만 — 테스트는 set_clock()으로 교체한다."""
import time

_clock = time.time


def now():
    return int(_clock())


def set_clock(fn):
    global _clock
    _clock = fn or time.time
