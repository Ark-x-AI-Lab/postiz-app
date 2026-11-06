# Maintain Postiz in a separate GitHub fork

Status: accepted, 6 November 2026, Mounssif.

Keep upstream history in Ark-x-AI-Lab/postiz-app and our deployment/customizations on deploy/arkx. Keep Social Studio in its existing repository. Build from reviewed source through the existing Coolify GitHub App. Select a released upstream version; do not deploy upstream main or mutable latest automatically.

Migration and cutover require backup/restore proof and an isolated rehearsal before retiring the existing service. This decision authorizes migration work, not publishing content, paid resources or broadening connector permissions.

Reason: patches and diagnostics become versioned, reproducible and reviewable. Upstream remains available for deliberate updates. Tradeoff: the team owns its small patch set and the compatibility checks for every upgrade.
