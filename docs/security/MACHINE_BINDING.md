# WatchLog secret store — machine-binding proof

**Property under test.** The recorder credential and agent key are stored as
Windows DPAPI blobs encrypted with `CRYPTPROTECT_LOCAL_MACHINE`
(`windows_secret.py:protect_bytes`). A LocalMachine blob is bound to the machine's
DPAPI master key: every elevated context on *that* Windows install can decrypt it
(the deterministic multi-context read requirement — elevated Setup, the SYSTEM
task, elevated Repair), but a copy of the blob taken to a **different** machine
must not decrypt.

## Why the two-hosted-runner CI job cannot prove this

GitHub-hosted `windows-latest` runners are provisioned by cloning **one** VM
image. The DPAPI LocalMachine master key material lives under
`C:\ProgramData\Microsoft\Crypto\` and is part of that image, so two hosted
runners share the same machine key. Empirically, in security-gate run
`34023896559`, the second runner **successfully decrypted** the first runner's
canary blob:

```
FAIL cross-machine decrypt is denied (machine binding)   # i.e. plain == CANARY
```

That is an **infrastructure property of hosted runners, not a WatchLog
regression** — two hosted runners are not two distinct machines for DPAPI
purposes. The gate's `machine-binding-decrypt` job is therefore **informational**
(`cross-machine-probe`): it records the outcome and never fails the gate.

## Genuine proof — distinct hardware

Machine binding is proven with two provably-different machines: a GitHub-hosted
runner and the developer's own Windows 11 workstation (physically distinct
hardware, its own DPAPI master key).

1. **Encrypt on machine A (hosted runner)** — security-gate run `34023896559`,
   job *Machine binding — encrypt on runner A*, step *Encrypt canary with
   LocalMachine DPAPI*. Produces `dpapi-canary-blob` (262 bytes,
   sha256 `73426f058dfd0fc1e2fb86495ca4c99e…`), uploaded as an artifact.

2. **Attempt decrypt on machine B (developer workstation)** — the artifact was
   downloaded to the workstation and decryption attempted with the same
   `CryptUnprotectData` path used in production:

   ```
   $ python prototype/tests/windows_security_gate.py decrypt-must-fail blob.bin
   PASS cross-machine decrypt is denied (machine binding)
   security gate step OK
   EXIT: 0
   ```

   The workstation — a different machine with a different DPAPI master key — could
   **not** decrypt the runner's blob. Machine binding holds.

## Reproducing

Run `decrypt-must-fail <blob>` on any machine other than the one that produced the
blob; it must print `PASS` and exit 0. Running it on the *same* machine (or a
clone that shares the DPAPI key) will instead decrypt and (correctly) report the
binding does not apply there.
