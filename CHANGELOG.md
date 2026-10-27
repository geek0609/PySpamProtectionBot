# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-27

This release follows version `2.0` of the OFD Specification and SpamProtectionBot 1.0.13 with PyFederation 2.0, it
is not compatible with servers implementing OFD 1.x.

### Added
 - Block Ads & Trackers, a scanning setting that also assesses advertising and tracking with the concerns the
   Federation server supports. It is on by default, also for chats configured before this release.
 - `/blacklist <identifier> <type> <expires>` without a reply blacklists an entity without a report.
 - `/evidence` and the evidence of assigned reports show whether evidence is automated, its content type and its
   context.
 - `/auth` and `/authinfo` show whether automated reports are assigned to the operator, apart from other reports.
 - A warning at startup when the Federation server implements another major version of the OFD Specification.

### Changed
 - The bot's own Federation operator is configured as a bot, so the evidence and reports it submits are automated.
   It must be a dedicated operator with client permissions: root and system are refused, and a disabled operator is
   treated as anonymous.
 - Operator permissions and settings are read from the `permissions` and `configuration` of OFD 2.0 operator
   records.

### Fixed
 - Only HTTP 404 is treated as an absent Federation record, a server error mentioning that something was not found
   is reported as an error.

## [1.0.0] - 2026-10-13

The first release, a port of [SpamProtectionBot](https://github.com/nosial/SpamProtectionBot) 1.0.13 to Python on
PyFederation and kurigram. This release follows version `v1.0-R4` of the OFD Specification.

### Added
 - Group protection: scanning, join protection, reporting and the configuration menu, with Federation Web Application
   links on notifications.
 - Secretary Mode for Telegram Business accounts, with a Scanning toggle that is off by default and a Report Message
   button on first-contact notifications.
 - Operator commands (`/auth`, `/authinfo`, `/deauth`, `/blacklist`, `/link`) and notifications of the reports
   assigned to an operator, which remove a stored credential the Federation server rejects.
 - Federation lookups (`/info`, `/evidence`, `/report <uuid>`) and the general commands (`/start`, `/settings`,
   `/help`, `/language`, `/ping`).
 - Authentication retries every 30 seconds while the Federation server cannot be reached at startup.
 - English, Chinese, Russian and Spanish localization.
