__all__ = ['TooMuchDataError']


class TooMuchDataError(ValueError):
    """Thrown when submitted data exceeds a defined limit."""
