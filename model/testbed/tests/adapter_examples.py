"""Deliberately broken adapters for boundary regression tests."""
import time


def inspect_public(request):
    from model.testbed.adapters import validate_request
    validate_request(request)
    print("A model may log to stdout")
    return {item["sku"]: [1.0] * request["horizon_days"] for item in request["items"]}


def missing_sku(request):
    return {}


def crashing(request):
    raise RuntimeError("intentional test failure")


def sleeping(request):
    time.sleep(5)


def mutating(request):
    result = inspect_public(request)
    request["items"].clear()
    return result
