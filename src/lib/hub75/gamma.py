"""Gamma correction modes. Pass an instance to `Hub75Driver(gamma=...)` or `Hub75Driver.set_gamma(...)`."""


class SRGB:
    """sRGB gamma correction (IEC 61966-2-1).

    Applies a linear segment for small values (`x <= 0.04045`, divided by 12.92)
    and a 2.4-power curve above that. This is the default gamma and is a good
    match for most RGB source material (photos, UI screenshots, sRGB framebuffers).
    """
    pass


class Power:
    """Simple power-function gamma correction (`output = input ** value`).

    Useful when you want a traditional CRT-style gamma curve (2.2 is the
    conventional default) or a custom exponent. A value of 1.0 is equivalent
    to disabling gamma correction.
    """

    def __init__(self, value: float = 2.2):
        """Create a power-function gamma.

        Args:
            value: Exponent applied to each normalized channel. Clamped to `>= 0.0`.
                Common choices: 2.2 (typical CRT-style gamma), 1.8 (older Mac gamma),
                1.0 (linear, no correction).
        """
        self._value = max(0.0, value)

    @property
    def value(self) -> float:
        """The exponent passed at construction (already clamped to `>= 0.0`)."""
        return self._value
