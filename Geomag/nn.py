from collections import OrderedDict
from typing import Any


class Module:
    """Lightweight callable base — inspired by ``torch.nn.Module``.

    Subclasses override ``forward()``; calling an instance delegates to it.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.forward(*args, **kwargs)

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class Sequential(Module):
    """Ordered container of ``Module`` instances chained via ``forward``."""

    def __init__(self, *modules: Any) -> None:
        self._modules: OrderedDict[str, Module] = OrderedDict()
        for i, module in enumerate(modules):
            if isinstance(module, tuple) and len(module) == 2:
                name, obj = module
            else:
                name, obj = str(i), module
            self._modules[str(name)] = obj

    def add_module(self, name: str, module: Module) -> None:
        self._modules[str(name)] = module

    def forward(self, x: Any) -> Any:
        out = x
        for module in self._modules.values():
            out = module(out)
        return out

    def named_modules(self) -> list[tuple[str, Module]]:
        return list(self._modules.items())
