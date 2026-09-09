"""Contract for the vendor capability matrix — honesty is the whole point.

No capability may read 'proven' unless it is implemented AND has documented field
evidence; a base no-op reads 'unsupported' (never dressed up as 'unverified'); the mock
simulator is never proven; recorded-footage retrieval is never proven (no clip API has
been validated against real hardware yet). Runs offline by introspecting the drivers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import vendor_capabilities as vc                     # noqa: E402
from drivers import DRIVERS                           # noqa: E402

M = vc.matrix()
VALID = {"proven", "unverified", "unsupported", "unknown"}


def _method_for(key):
    for k, method, _ in vc.CAPABILITIES:
        if k == key:
            return method
    return None


def test_every_registered_driver_present():
    assert set(M["devices"]) == set(DRIVERS), "matrix must cover exactly the registered drivers"


def test_statuses_are_valid():
    for name, d in M["devices"].items():
        for key, cell in d["capabilities"].items():
            assert cell["status"] in VALID, f"{name}.{key} invalid status {cell['status']!r}"


def test_proven_requires_evidence_and_implementation():
    for name, d in M["devices"].items():
        for key, cell in d["capabilities"].items():
            if cell["status"] == "proven":
                assert (name, key) in vc.FIELD_PROVEN, f"{name}.{key} proven without FIELD_PROVEN evidence"
                assert cell["evidence"], f"{name}.{key} proven without evidence text"
                method = _method_for(key)
                assert method is None or vc._implemented(DRIVERS[name], method), \
                    f"{name}.{key} cannot be proven — driver does not implement it"


def test_mock_is_never_proven():
    for key, cell in M["devices"]["mock"]["capabilities"].items():
        assert cell["status"] != "proven", f"mock simulator must never be proven ({key})"


def test_footage_retrieval_never_proven():
    for name, d in M["devices"].items():
        assert d["capabilities"]["footage_retrieval"]["status"] != "proven", \
            f"{name} claims proven footage retrieval without hardware validation"


def test_base_noop_reads_unsupported():
    for name in M["devices"]:
        for key, method, _ in vc.CAPABILITIES:
            if not vc._implemented(DRIVERS[name], method):
                assert M["devices"][name]["capabilities"][key]["status"] == "unsupported", \
                    f"{name}.{key} is a base no-op but reads " \
                    f"{M['devices'][name]['capabilities'][key]['status']!r}"


def test_transport_targets_declared_not_faked():
    for k in ("port_classification", "https_self_signed", "multi_nic_subnet"):
        assert M["transport"][k]["status"] in ("unsupported", "unverified", "unknown"), \
            f"transport {k} must not claim support it lacks"


if __name__ == "__main__":
    for _name, _fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        _fn()
    print("Vendor capability matrix contract: PASS")
    print()
    print(vc.render_markdown())
