"""A trusted Python adapter runs here with only the public request on stdin."""
import contextlib
import importlib
import json
import sys


def load_callable(spec):
    module_name, name = spec.split(":", 1)
    function = getattr(importlib.import_module(module_name), name)
    if not callable(function):
        raise ValueError("Adapter is not callable")
    return function


def main():
    request = json.load(sys.stdin)
    # Model log messages cannot corrupt the JSON transport.
    with contextlib.redirect_stdout(sys.stderr):
        result = load_callable(sys.argv[1])(request)
    json.dump(result, sys.stdout, allow_nan=False)


if __name__ == "__main__":
    main()
