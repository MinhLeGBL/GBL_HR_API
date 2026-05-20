# Changelog — health

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- API health (`GET /api/v1/health/`)
- Database health (`GET /api/v1/health/database`) — verifies PostgreSQL
  and Oracle connectivity
