# APT release automation

[Back to README](../README.md) · [Building and testing](development.md)

Camera Bridge publishes `camera-bridge_VERSION_all.deb` to the existing signed
M45 Software archive at <https://m45software.com/apt/> (`stable`, `main`, amd64).
Architecture-independent packages are included in that index.

## Release flow

1. Commit and push changes, including `docs/releases/vVERSION.md`.
2. Push `vVERSION` (for example `v0.2.0`). The release workflow validates the shell,
   runtime, Qt UI, and APT publisher/receiver before building the matching `.deb`.
3. It creates a GitHub release with the package and `SHA256SUMS`, then calls
   `publish-apt.yml` directly. This direct call is required because release events
   created with `GITHUB_TOKEN` do not trigger another workflow.
4. CI downloads the published package, checks its checksum, and streams it over
   the restricted SSH connection. The server independently checks package name,
   version, architecture, byte count, and hash before publishing signed metadata.

Manually published releases also trigger publication. Drafts do not publish.
Use **Actions → Publish APT repository → Run workflow** on `main` with
**check_only** enabled to test the connection. To retry publication, disable it
and specify the existing release tag. Identical packages are safe to retry;
replacing an existing version with different bytes is rejected.

## Deployment configuration

`M45_APT_DEPLOY_KEY` is a dedicated repository Actions secret. Its public key has
an SSH `restrict` entry with a forced command pointing to:

`/home/dist/.local/share/m45-apt/receive-camera-bridge-release.py`

It can publish only `camera-bridge` packages with architecture `all`, up to
64 MiB. It cannot open a shell, upload arbitrary paths, or forward connections.
`packaging/apt/ssh_known_hosts` pins the verified server host key. Never replace
that pin without checking the new key independently.

The receiver invokes `/home/dist/.local/share/m45-apt/publish-apt.py`, which
accepts the existing `m45-*` packages plus the explicit `camera-bridge` name.
Archive signing uses the existing private key under
`/home/dist/.local/share/m45-apt/gnupg`. It stays on the server, separate from the
web root and GitHub. The public signing-key fingerprint remains:

`F8BD 2FC4 4347 3EB7 1D87 003B F8A7 E75C 58D7 C0A3`

The archive publisher serializes updates, retains old hash-addressed indexes for
concurrent clients, and replaces `InRelease` atomically. Existing packages and
signing identity are preserved.

Source copies of the publisher and Camera Bridge receiver are in `scripts/`.
They are server-side deployment utilities, not installed by the desktop `.deb`.
Before replacing a shared server publisher, review its current contents so changes
for other M45 applications are preserved.
