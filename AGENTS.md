# Agents working on this fork

This is **Cornyhorse's hard fork** of TubeArchivist (base: upstream `v0.5.12`),
for private use on Matt's homelab. It is not a contribution to upstream.

Rules:

- **Never interact with upstream.** No pushes, PRs, issues or comments on
  `tubearchivist/tubearchivist`. The `upstream` remote is fetch-only.
- Work happens on branches of `cornyhorse/tubearchivist` and is reviewed by Matt.
- GPL-3.0 still applies. Any image we publish must have its source published here.
- Design notes are in `docs/fork/`. Read `docs/fork/postgres-storage-design.md`
  before touching `backend/storage/` or `backend/emby/`.
- Tests run against throwaway services only: `docker compose -f
  docker-compose.dev-pg.yml up -d` (Postgres on 127.0.0.1:55432, Redis on 56379).
- Never point tests or tools at production media
  (`/mnt/wd12tb/media/tubearchivist`), production ES or production Emby. Use a
  read-only sample and a scratch output root.
