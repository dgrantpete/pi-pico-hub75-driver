from machine import Pin

class Binary:
    def __init__(self, base_pin: Pin, bit_count: int):
        self._base_pin = base_pin
        self._bit_count = bit_count

    @property
    def base_pin(self) -> Pin:
        return self._base_pin
    
    @property
    def bit_count(self) -> int:
        return self._bit_count
    
class ShiftRegister:
    def __init__(self, data_pin: Pin, clock_pin: Pin, depth: int, clock_frequency: int | None = None):
        self._data_pin = data_pin
        self._clock_pin = clock_pin
        self._depth = depth
        self._clock_frequency = clock_frequency

    @property
    def data_pin(self) -> Pin:
        return self._data_pin

    @property
    def clock_pin(self) -> Pin:
        return self._clock_pin

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def clock_frequency(self) -> int | None:
        return self._clock_frequency