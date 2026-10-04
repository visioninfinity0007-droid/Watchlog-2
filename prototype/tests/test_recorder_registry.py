"""Multi-recorder local registry + credential-store contracts.

Cross-platform orchestration tests use a fake filesystem crypto layer; Windows CI
separately proves the actual DPAPI primitive and DACL behavior.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import windows_secret as ws  # noqa: E402


def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError(f"corrupt DPAPI blob at {path}")
        return json.loads(text[5:])

    cs.write_json_secret = wjs
    cs.read_json_secret = rjs
    cs.unprotect_bytes = lambda b: b


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        _install_fake_crypto()
        return Path(self.tmp.name) / "WatchLog"

    def __exit__(self, *args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def test_per_recorder_credentials_are_independent():
    with _Env():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        cs.save_recorder_credential(a, "alice", "pw-a")
        cs.save_recorder_credential(b, "bob", "pw-b")

        assert cs.load_recorder_credential(a)["password"] == "pw-a"
        assert cs.load_recorder_credential(b)["password"] == "pw-b"
        assert cs.recorder_credential_path(a) != cs.recorder_credential_path(b)
        assert cs.recorder_credential_generation(a) != "absent"
        assert cs.recorder_credential_generation(b) != "absent"


def test_corrupt_recorder_credential_never_falls_back_to_singleton_or_sibling():
    with _Env():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        cs.save_nvr_credential("legacy", "legacy-pw")
        cs.save_recorder_credential(a, "alice", "pw-a")
        cs.save_recorder_credential(b, "bob", "pw-b")
        cs.recorder_credential_path(b).write_text("CORRUPT", encoding="utf-8")

        assert cs.load_recorder_credential(a)["password"] == "pw-a"
        try:
            cs.load_recorder_credential(b)
            assert False, "corrupt Recorder B credential must fail closed"
        except ws.SecretError:
            pass
        assert cs.load_nvr_credential()["password"] == "legacy-pw"


def test_legacy_singleton_staging_is_copy_only_and_idempotent():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text(
            "[watchlog]\n"
            "nvr_url = http://192.0.2.10\n"
            "nvr_username = operator\n"
            "nvr_driver = onvif\n",
            encoding="utf-8",
        )
        cs.save_nvr_credential("operator", "legacy-pw")

        row = rr.migrate_legacy_singleton(ini)
        assert row is not None and row["is_primary"] is True
        local_id = row["local_id"]
        uuid.UUID(local_id)

        # New artifacts exist and decrypt.
        assert rr.registry_path().exists()
        assert cs.load_recorder_credential(local_id)["password"] == "legacy-pw"

        # Compatibility phase is copy-only: old 5.0.27 runtime still has exactly
        # the singleton inputs it expects.
        assert cs.nvr_credential_path().exists()
        assert "nvr_url = http://192.0.2.10" in ini.read_text(encoding="utf-8")

        first_registry = rr.registry_path().read_bytes()
        second = rr.migrate_legacy_singleton(ini)
        assert second["local_id"] == local_id
        assert rr.registry_path().read_bytes() == first_registry


def test_registry_contains_no_username_or_password():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text(
            "[watchlog]\nnvr_url = http://192.0.2.20\nnvr_driver = auto\n",
            encoding="utf-8",
        )
        cs.save_nvr_credential("secret-user", "secret-password")
        rr.migrate_legacy_singleton(ini)

        raw = rr.registry_path().read_text(encoding="utf-8")
        assert "secret-user" not in raw
        assert "secret-password" not in raw
        payload = json.loads(raw)
        assert payload["schema"] == rr.REGISTRY_SCHEMA
        assert len(payload["recorders"]) == 1


def test_add_second_recorder_keeps_secrets_out_of_registry():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text(
            "[watchlog]\nnvr_url = http://192.0.2.30\nnvr_driver = onvif\n",
            encoding="utf-8",
        )
        cs.save_nvr_credential("primary", "primary-pw")
        primary = rr.migrate_legacy_singleton(ini)

        second = rr.add_recorder(
            display_name="Loading Dock Recorder",
            url="http://192.0.2.31",
            driver="onvif",
            username="dock-user",
            password="dock-pw",
            is_primary=False,
            vendor="Dahua",
            model="TEST",
        )

        rows = rr.recorders()
        assert len(rows) == 2
        assert rr.primary_recorder()["local_id"] == primary["local_id"]
        assert cs.load_recorder_credential(second["local_id"])["password"] == "dock-pw"

        raw = rr.registry_path().read_text(encoding="utf-8")
        assert "dock-user" not in raw and "dock-pw" not in raw


def test_duplicate_local_address_is_rejected_without_extra_credential():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text(
            "[watchlog]\nnvr_url = http://192.0.2.40\nnvr_driver = auto\n",
            encoding="utf-8",
        )
        cs.save_nvr_credential("admin", "pw")
        rr.migrate_legacy_singleton(ini)

        before = set(p.name for p in cs.recorder_secrets_dir().glob("*.dpapi"))
        try:
            rr.add_recorder(
                display_name="Duplicate",
                url="http://192.0.2.40",
                driver="auto",
                username="x",
                password="y",
            )
            assert False, "duplicate local address must be rejected"
        except ValueError:
            pass
        after = set(p.name for p in cs.recorder_secrets_dir().glob("*.dpapi"))
        assert after == before


def test_registry_rejects_multiple_configured_primaries():
    with _Env():
        rows = []
        for name in ("A", "B"):
            rows.append({
                "local_id": str(uuid.uuid4()),
                "display_name": name,
                "url": "",
                "driver": "auto",
                "is_primary": True,
                "is_configured": True,
            })
        try:
            rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": rows})
            assert False, "multiple primary recorders must fail"
        except ValueError:
            pass


def test_cloud_mapping_binds_each_local_recorder_once():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text("[watchlog]\nnvr_url = http://192.0.2.50\nnvr_driver = auto\n", encoding="utf-8")
        cs.save_nvr_credential("admin", "pw")
        primary = rr.migrate_legacy_singleton(ini)
        second = rr.add_recorder(
            display_name="Recorder B",
            url="http://192.0.2.51",
            driver="onvif",
            username="b",
            password="pw-b",
        )

        a_cloud = "11111111-1111-1111-1111-111111111111"
        b_cloud = "22222222-2222-2222-2222-222222222222"
        rr.apply_cloud_mapping({
            primary["local_id"]: a_cloud,
            second["local_id"]: b_cloud,
        })

        assert rr.recorder(primary["local_id"])["cloud_recorder_id"] == a_cloud
        assert rr.recorder(second["local_id"])["cloud_recorder_id"] == b_cloud

        # Idempotent replay is safe.
        rr.apply_cloud_mapping({
            primary["local_id"]: a_cloud,
            second["local_id"]: b_cloud,
        })


def test_cloud_mapping_rejects_unknown_duplicate_and_identity_drift():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text("[watchlog]\nnvr_url = http://192.0.2.60\nnvr_driver = auto\n", encoding="utf-8")
        cs.save_nvr_credential("admin", "pw")
        primary = rr.migrate_legacy_singleton(ini)
        second = rr.add_recorder(
            display_name="Recorder B",
            url="http://192.0.2.61",
            driver="auto",
            username="b",
            password="pw-b",
        )

        a_cloud = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        b_cloud = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        try:
            rr.apply_cloud_mapping({str(uuid.uuid4()): a_cloud})
            assert False, "unknown local recorder must be rejected"
        except ValueError:
            pass

        try:
            rr.apply_cloud_mapping({
                primary["local_id"]: a_cloud,
                second["local_id"]: a_cloud,
            })
            assert False, "duplicate cloud recorder id must be rejected"
        except ValueError:
            pass

        rr.apply_cloud_mapping({primary["local_id"]: a_cloud})
        try:
            rr.apply_cloud_mapping({primary["local_id"]: b_cloud})
            assert False, "cloud identity drift must fail closed"
        except ValueError:
            pass


def test_observed_identity_update_is_local_and_non_secret():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        ini = wl / "watchlog.ini"
        ini.write_text("[watchlog]\nnvr_url = http://192.0.2.70\nnvr_driver = auto\n", encoding="utf-8")
        cs.save_nvr_credential("secret-user", "secret-password")
        primary = rr.migrate_legacy_singleton(ini)

        updated = rr.update_observed_identity(
            primary["local_id"],
            vendor="Hikvision",
            model="DS-TEST",
            firmware="V1",
            driver="onvif",
            identity_fingerprint="serial:abc",
        )
        assert updated["vendor"] == "Hikvision"
        assert updated["model"] == "DS-TEST"
        assert updated["driver"] == "onvif"
        raw = rr.registry_path().read_text(encoding="utf-8")
        assert "secret-user" not in raw
        assert "secret-password" not in raw



def test_registry_rejects_disabled_continuity_owner():
    with _Env():
        local_id = str(uuid.uuid4())
        cs.save_recorder_credential(local_id, "admin", "pw")
        try:
            rr.save_registry({
                "schema": rr.REGISTRY_SCHEMA,
                "recorders": [{
                    "local_id": local_id,
                    "display_name": "Original Recorder",
                    "url": "http://192.0.2.80",
                    "driver": "onvif",
                    "is_primary": False,
                    "continuity_owner": True,
                    "is_configured": False,
                }],
            })
            assert False, "disabled continuity owner must be rejected"
        except ValueError as exc:
            assert "continuity owner must remain configured" in str(exc).lower()
