import micropython
import framebuf
from .driver import Hub75Driver


class Hub75Display(framebuf.FrameBuffer):
    """RGB565 `framebuf.FrameBuffer` view over a `Hub75Driver`.

    A convenience wrapper that gives you the full MicroPython `FrameBuffer`
    drawing API (`pixel`, `line`, `rect`, `ellipse`, `text`, `blit`, ...) backed
    by a dedicated RGB565 buffer, plus a single `show()` call that pushes the
    buffer to the driver and flips. The backing buffer is sized
    `width * height * 2` bytes.

    Because this subclasses `framebuf.FrameBuffer`, it is drop-in compatible
    with any library that consumes one (e.g. `writer.py`, `CWriter`).

    Example:
        display = Hub75Display(driver)
        display.fill(0)
        display.rect(10, 10, 20, 20, 0xF800, True)  # red rectangle
        display.show()
    """

    @micropython.native
    def __init__(self, driver: Hub75Driver, width=None):
        """Create a framebuffer view backed by the given driver.

        Args:
            driver: The `Hub75Driver` this display will write to on `show()`.
            width: Display width in pixels. When `None` (the default), width
                is inferred as `driver.shift_register_depth`, assuming the
                standard indoor-panel layout of two rows lit per address
                (so `height = driver.row_address_count * 2`). Override this
                for outdoor panels or chained / non-standard geometries where
                the pixel arrangement doesn't match that assumption; `height`
                is then derived as
                `shift_register_depth * row_address_count * 2 / width`.
        """
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
        """Display width in pixels."""
        return self._width

    @property
    @micropython.native
    def height(self) -> int:
        """Display height in pixels."""
        return self._height

    @property
    @micropython.native
    def buffer(self) -> bytearray:
        """The backing RGB565 byte buffer (`width * height * 2` bytes), exposed for direct blitting."""
        return self._buffer

    @micropython.native
    def show(self) -> None:
        """Push the current buffer to the driver and flip.

        Equivalent to `driver.load_rgb565(buffer)` followed by `driver.flip()`.
        Call this after drawing to make your changes visible on the panel.
        """
        self._driver.load_rgb565(self._buffer)
        self._driver.flip()
