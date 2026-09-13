"""Bounded snapshot history, independent of Qt and canvas events."""
from copy import deepcopy


class EditHistory:
    def __init__(self, limit=100):
        self.limit = limit
        self.past = []
        self.future = []

    def clear(self):
        self.past.clear()
        self.future.clear()

    def record(self, before):
        self.past.append(deepcopy(before))
        del self.past[:-self.limit]
        self.future.clear()

    def undo(self, current):
        if not self.past:
            raise IndexError('No edit to undo')
        self.future.append(deepcopy(current))
        return self.past.pop()

    def redo(self, current):
        if not self.future:
            raise IndexError('No edit to redo')
        self.past.append(deepcopy(current))
        return self.future.pop()
