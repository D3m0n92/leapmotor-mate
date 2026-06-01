# Changelog

All notable changes to LeapMotor Mate are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.1] — 2026-06-01

### Fixed
- **Home Assistant ingress support**: the web UI now works inside the add-on
  panel. URLs are resolved against the ingress path via `<base href>` (from the
  `X-Ingress-Path` header) and all template/JS URLs are relative; server
  redirects carry the ingress prefix. Standalone is unaffected.

## [1.0.0] — 2026-06-01

First public release.

### Added
- Trip tracking with route map, distance, energy, efficiency and regen.
- Charge logging with AC/DC detection, energy added, power and distribution chart.
- Statistics: driving/AC/other energy split and a 6-week consumption trend (Leapmotor cloud).
- Remote control: lock, windows, trunk, panoramic roof, climate, find car, battery preheat.
- Two-step setup wizard: app certificate (upload/paste) + account login with EU model/battery auto-detect.
- Configurable polling (parked/driving), bilingual UI (EN/IT).
- Home Assistant add-on and standalone Docker deployment.

[1.0.1]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.1
[1.0.0]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.0
