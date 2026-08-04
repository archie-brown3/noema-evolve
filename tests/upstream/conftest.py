"""Inventory-only donor dumps — block collection even if this path is passed explicitly."""


def pytest_ignore_collect(collection_path, config):
    return True
