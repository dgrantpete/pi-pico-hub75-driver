import micropython
import framebuf
from .driver import Hub75Driver

class Hub75Display(framebuf.FrameBuffer):
    @micropython.native
    def __init__(self, driver: Hub75Driver, width=None):
        self._driver = driver

        if width is not None:
            self._width = width
            self._height = (driver.shift_register_depth * driver.row_address_count * 2) // width
        else:
            # Assumes that each address only drives a single row (e.g. 1/32 scan for 64-row panel) if width is not specified
            self._width = driver.shift_register_depth
            self._height = driver.row_address_count * 2

        self._buffer = bytearray(self._width * self._height * 2)
        super().__init__(self._buffer, self._width, self._height, framebuf.RGB565)

    @property
    @micropython.native
    def width(self) -> int:
        return self._width

    @property
    @micropython.native
    def height(self) -> int:
        return self._height
    
    @property
    @micropython.native
    def buffer(self) -> bytearray:
        return self._buffer

    @micropython.native
    def show(self) -> None:
        self._driver.load_rgb565(self._buffer)
        self._driver.flip()
