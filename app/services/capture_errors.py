"""Typed capture failures eligible for bounded device recovery."""
class IncompletePioPair(RuntimeError):
    def __init__(self, actual, expected):
        self.actual, self.expected = actual, expected
        super().__init__(f'Neúplná PIO dvojica: {actual}/{expected} snímok.')
