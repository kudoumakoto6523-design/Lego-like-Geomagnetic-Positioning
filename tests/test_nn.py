"""Tests for Geomag.nn — Module and Sequential abstractions."""

import pytest

from Geomag.nn import Module, Sequential


class AddOne(Module):
    def forward(self, x):
        return x + 1


class MultiplyBy(Module):
    def __init__(self, factor):
        self.factor = factor

    def forward(self, x):
        return x * self.factor


class TestModule:
    def test_call_delegates_to_forward(self):
        m = AddOne()
        assert m(5) == 6

    def test_not_implemented(self):
        m = Module()
        with pytest.raises(NotImplementedError):
            m(5)


class TestSequential:
    def test_empty(self):
        seq = Sequential()
        assert seq(42) == 42

    def test_single_module(self):
        seq = Sequential(AddOne())
        assert seq(0) == 1

    def test_chain(self):
        seq = Sequential(AddOne(), MultiplyBy(3), AddOne())
        # ((0 + 1) * 3) + 1 = 4
        assert seq(0) == 4

    def test_named_modules(self):
        seq = Sequential(("add", AddOne()), ("mul", MultiplyBy(3)))
        names = [name for name, _ in seq.named_modules()]
        assert names == ["add", "mul"]

    def test_add_module(self):
        seq = Sequential()
        seq.add_module("add", AddOne())
        seq.add_module("mul", MultiplyBy(2))
        # (5 + 1) * 2 = 12
        assert seq(5) == 12

    def test_mixed_construction(self):
        # nameless then named
        seq = Sequential(AddOne(), ("mul", MultiplyBy(4)))
        # (3 + 1) * 4 = 16
        assert seq(3) == 16
