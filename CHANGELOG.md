# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
