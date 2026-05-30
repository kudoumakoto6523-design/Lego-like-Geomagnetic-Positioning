"""Tests for Geomag.blocks — Registry and block implementations."""

import pytest

from Geomag.blocks import (
    Registry,
    AlgoStepJudge,
    AlgoStepLength,
    AlgoHeading,
    AlgoMag,
    AlwaysTrigger,
    describe_callable_params,
)


class TestRegistry:
    def test_register_and_build(self):
        reg = Registry("test")

        @reg.register("my_block")
        def _build_my_block(**kwargs):
            return {"type": "my_block", "kwargs": kwargs}

        result = reg.build("my_block", param_a=1, param_b=2)
        assert result == {"type": "my_block", "kwargs": {"param_a": 1, "param_b": 2}}

    def test_unknown_key_raises(self):
        reg = Registry("test")
        with pytest.raises(ValueError, match="Unknown test block"):
            reg.build("nonexistent")

    def test_keys(self):
        reg = Registry("test")

        @reg.register("alpha")
        def _alpha():
            pass

        @reg.register("beta")
        def _beta():
            pass

        assert reg.keys() == ["alpha", "beta"]

    def test_describe(self):
        reg = Registry("test")

        @reg.register("my_block", param_docs={"x": "param x"})
        def _my_block(x=1):
            pass

        desc = reg.describe()
        assert "my_block" in desc
        assert desc["my_block"]["params"] == {"x": "param x"}

    def test_case_insensitive_key(self):
        reg = Registry("test")

        @reg.register("MyBlock")
        def _my_block():
            return 42

        assert reg.build("myblock") == 42
        assert reg.build("MYBLOCK") == 42

    def test_decorator_returns_builder(self):
        reg = Registry("test")

        @reg.register("block")
        def _block():
            return "ok"

        # The decorator should return the original function
        assert _block() == "ok"


class TestDescribeCallableParams:
    def test_simple_function(self):
        def fn(a, b=10, c="hello"):
            pass

        params = describe_callable_params(fn)
        assert params == {"a": None, "b": 10, "c": "hello"}

    def test_method_skips_self(self):
        class Foo:
            def method(self, x, y=5):
                pass

        params = describe_callable_params(Foo().method)
        assert params == {"x": None, "y": 5}
        assert "self" not in params


class TestAlwaysTrigger:
    def test_always_true(self):
        trigger = AlwaysTrigger()
        assert trigger.should_resample(None, target_count=100) is True
        assert trigger.should_resample(None, target_count=0) is True
