"""Row addressing schemes for HUB75 panels.

Pass an instance of `Binary`, `ShiftRegister`, or `Direct` to the
`row_addressing=` argument of `Hub75Driver` to tell the driver how the panel
selects which row(s) are lit at any given moment. If you're unsure which one
your panel uses, start with `Binary` — it is by far the most common.
"""

from machine import Pin


class Binary:
    """Binary-encoded row addressing (most common).

    The panel has N address pins (A, B, C, [D, [E]]) that encode the active
    row as a binary number, selecting `1 << bit_count` rows total. Used by
    virtually all standard indoor panels.
    """

    def __init__(self, base_pin: Pin, bit_count: int):
        """Configure binary row addressing.

        Args:
            base_pin: The GPIO connected to address line A (the least-significant
                address bit). The remaining address lines (B, C, ...) must be on
                consecutive GPIOs immediately above this one.
            bit_count: Number of address pins on the panel. Determines the scan
                rate: 3 pins → 1/8 scan, 4 pins → 1/16 scan, 5 pins → 1/32 scan.
                The driver will cycle through `2 ** bit_count` row addresses.
        """
        self._base_pin = base_pin
        self._bit_count = bit_count

    @property
    def base_pin(self) -> Pin:
        """GPIO connected to address line A (lowest address bit)."""
        return self._base_pin

    @property
    def bit_count(self) -> int:
        """Number of address pins; `2 ** bit_count` is the row address count."""
        return self._bit_count


class ShiftRegister:
    """Shift-register row addressing (some outdoor / very-large panels).

    Instead of binary address pins, these panels have a shift register whose
    outputs drive row-select lines one at a time. The driver clocks a single
    '1' through the register to walk through row addresses. On the HUB75
    connector: pin A is the shift clock, pin B is the enable (active low —
    hold it low externally, e.g. tie to GND), and pin C is the data input.

    If your panel doesn't work with `Binary` (only one row lights, or rows
    appear in the wrong order), try this type.
    """

    def __init__(self, data_pin: Pin, clock_pin: Pin, depth: int, clock_frequency: int | None = None):
        """Configure shift-register row addressing.

        Args:
            data_pin: GPIO driving the shift register's data input (HUB75 pin C).
            clock_pin: GPIO driving the shift register's clock (HUB75 pin A).
            depth: Number of addressable rows on the panel (equivalent to the
                scan-rate denominator — e.g. 32 for a 1/32 scan panel).
            clock_frequency: Optional shift-register clock frequency in Hz.
                When `None` (the default), the shift register is clocked at the
                same rate as the driver's `data_frequency`. Set this explicitly
                if your shift register IC needs a slower clock. The driver will
                raise `ValueError` during construction if the requested
                frequency is too low to achieve with the available PIO delay
                slots (the error message reports the minimum achievable value).
        """
        self._data_pin = data_pin
        self._clock_pin = clock_pin
        self._depth = depth
        self._clock_frequency = clock_frequency

    @property
    def data_pin(self) -> Pin:
        """GPIO wired to HUB75 pin C (shift register data input)."""
        return self._data_pin

    @property
    def clock_pin(self) -> Pin:
        """GPIO wired to HUB75 pin A (shift register clock)."""
        return self._clock_pin

    @property
    def depth(self) -> int:
        """Number of addressable rows that the shift register walks through."""
        return self._depth

    @property
    def clock_frequency(self) -> int | None:
        """Configured shift-register clock in Hz, or `None` to inherit `data_frequency`."""
        return self._clock_frequency


class Direct:
    """Direct (one-hot) row addressing.

    Each row has its own dedicated address line, and the driver asserts exactly
    one line at a time. Use this for panels that expose an individual select
    line per row rather than an encoded binary address or a shift-register
    chain.
    """

    def __init__(self, base_pin: Pin, address_count: int):
        """Configure direct row addressing.

        Args:
            base_pin: GPIO connected to the first row-select line. The
                remaining `address_count - 1` row-select GPIOs must be on
                consecutive pins immediately above this one.
            address_count: Number of dedicated row-select lines (equal to the
                number of addressable rows the driver will cycle through).
        """
        self._base_pin = base_pin
        self._address_count = address_count

    @property
    def base_pin(self) -> Pin:
        """GPIO connected to the first (lowest) row-select line."""
        return self._base_pin

    @property
    def address_count(self) -> int:
        """Number of dedicated row-select pins; also the number of addressable rows."""
        return self._address_count