# Minecraft container trust store maintenance

The host CA store and the Bedrock container CA store are separate. The vendor's
`latest` image was last published on 2026-10-13 when checked on 2026-10-22 JST;
pulling that tag does not apply the subsequent eMudhra distrust update.

`docker/minecraft-security/Dockerfile` derives from the **currently installed
digest-pinned vendor image** and applies signed distribution updates. The input
image must use Debian 13 and its original build user must be root. Verify the
new image's architecture, entrypoint, command, healthcheck and volumes against
that image before changing any live configuration. Verify the CA version and
the absence of both affected root fingerprints in the built image. Do not copy
live container contents into an image or use `docker commit`.

Keep the running Bedrock version and existing data volume. The vendor supports
`VERSION=EXISTING` to reuse the installed executable; this avoids a game upgrade
during an OS-package-only maintenance. Use it only for the maintenance restart,
then restore the site's existing VERSION policy in Compose without restarting
again. In particular, the installed auto-updater expects VERSION=LATEST; do not
silently disable subsequent game updates. Record the previous image and exact
configuration, and use the existing backup, lock, managed Control API restart
and rollback procedures. A successful build alone is not a completed rollout.
After rollout, verify Bedrock's UDP status, version, existing packs, data mounts
and application connectivity. Never reinitialize or replace a world.

Sources: [vendor image and VERSION documentation](https://github.com/itzg/docker-minecraft-bedrock-server),
[Debian CA advisory](https://security-tracker.debian.org/tracker/DSA-6572-1),
[incident and root-program response links](https://emudhra.we-have-a-problem.com/).
