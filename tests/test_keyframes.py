import numpy as np

from rewind.keyframes import Settings, pick, still_runs

SETTINGS = Settings(fps=10, still_seconds=0.4, min_change=60)


def screen(value: int) -> np.ndarray:
    return np.full((180, 320), value, np.uint8)


def with_cursor(frame: np.ndarray, x: int) -> np.ndarray:
    frame = frame.copy()
    frame[50:55, x:x + 3] = 0  # a 3x5 cursor, like the arrow at 1/4 scale
    return frame


def test_still_runs_need_minimum_length():
    a, b = screen(100), screen(200)
    frames = np.stack([a, a, a, b, b, b, b, b])
    assert still_runs(frames, SETTINGS) == [(3, 8)]


def test_page_change_makes_a_new_keyframe():
    login, menu = screen(100), screen(200)
    frames = np.stack([login] * 6 + [menu] * 6)
    assert pick(frames, SETTINGS) == [2, 8]


def test_cursor_move_alone_is_not_a_keyframe():
    page = screen(150)
    frames = np.stack([with_cursor(page, 10)] * 6 + [with_cursor(page, 200)] * 6)
    assert pick(frames, SETTINGS) == [2]


def test_moving_cursor_is_not_still():
    page = screen(150)
    frames = np.stack([with_cursor(page, x) for x in range(0, 60, 6)])
    assert still_runs(frames, SETTINGS) == []
