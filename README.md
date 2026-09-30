# SpamProtectionBot

SpamProtectionBot is a Telegram bot that protects group chats and personal inboxes from spam, phishing, and abuse,
backed by the [Open Federated Database](https://github.com/Nosial/OFD-Specification) specification. The bot itself
holds no spam data of its own: every scan, entity lookup, and report is delegated to a Federation server (see
[FederationLib](https://github.com/nosial/FederationLib) for a reference implementation you can self-host), and the
bot is designed to keep working, in a reduced form, even when no Federation server is configured at all.

## Features

- **Scanning**: every member message in a protected group is checked against Federation for spam, phishing,
  malware, and other threats, with a configurable response ranging from notify-only to delete-and-ban.
- **Join Protection**: new members are checked against Federation the moment they join, so known bad actors can be
  restricted, banned, or have their join request declined before they cause damage.
- **Reporting**: members can flag a message with `/report`, filing evidence with the Federation network and
  notifying moderators in one step.
- **Secretary Mode**: connects to a personal Telegram Business account to screen first-time contacts before they
  reach your inbox, independently of group protection.
- **Privacy Mode**: a per-chat switch that minimizes what is published to Federation (no automatic entity
  publishing, scan submissions carry only message content) while the features that need data to work still send the
  minimum required.
- **Operator tooling**: `/auth`, `/blacklist`, and `/link` let Federation operators manage entities and close
  assigned reports without leaving Telegram, using their own credentials rather than the bot's.
- **Anonymous or authenticated**: the bot runs against a Federation server with or without its own access token;
  see [Federation Authentication](#federation-authentication) for what changes between the two.
- **Multi-language**: every user-facing string is served from a translation file discovered in the package; see
  [Adding a Language](#adding-a-language).

## Table of Contents

<!-- TOC -->
* [SpamProtectionBot](#spamprotectionbot)
  * [Features](#features)
  * [Table of Contents](#table-of-contents)
  * [Building & Installing](#building--installing)
    * [Requirements](#requirements)
    * [Building from Source](#building-from-source)
    * [Docker Usage](#docker-usage)
    * [Docker Compose Usage](#docker-compose-usage)
    * [Command-Line Interface](#command-line-interface)
  * [Configuration](#configuration)
    * [Bot Configuration](#bot-configuration)
    * [Federation Configuration](#federation-configuration)
      * [Federation Web Application](#federation-web-application)
    * [Federation Authentication](#federation-authentication)
  * [Setting Up the Bot in Telegram](#setting-up-the-bot-in-telegram)
    * [Group Protection](#group-protection)
    * [Secretary Mode](#secretary-mode)
  * [Commands](#commands)
    * [General](#general)
    * [Chat Protection](#chat-protection)
    * [Federation Lookups](#federation-lookups)
    * [Federation Operators](#federation-operators)
  * [Adding a Language](#adding-a-language)
* [License](#license)
<!-- TOC -->

## Building & Installing

There are a few approaches to deploying SpamProtectionBot, depending on whether you run the package directly or use
Docker, and whether you connect to an existing Federation server or host your own. This section covers building the
bot, running it, and the options it accepts; the file it reads at startup is described in
[Configuration](#configuration).

### Requirements

- Python 3.13 or later. Not needed when running the Docker image.
- A Telegram bot token issued by [@BotFather](https://t.me/BotFather).
- A Telegram API ID and hash issued at [my.telegram.org](https://my.telegram.org). The bot connects to Telegram over
  MTProto, which needs both alongside the bot token.
- Optionally, a running Federation server (such as FederationLib) to enable spam-protection features. The bot starts
  and answers commands without one, but scanning, join protection, and reporting have nothing to check against.

### Building from Source

The project installs with pip, which takes [PyFederation](https://pypi.org/project/pyfederation/) from PyPI, and
provides an `spb` command:

```shell
pip install .
```

`pip install -e . --group dev` installs the development tools as well; `make check` then runs the
linter and type checks, and `make test` runs the test suite. Start the bot by pointing it at its configuration file
and database:

```shell
spb --config /etc/spb/configuration.yml --database /var/lib/spb/database.db
```

Both paths have defaults, so the bot can also be started with no arguments at all from a directory that already
has a `configuration.yml` in it. Neither the configuration file nor the database should be committed to version
control, since the former holds your bot token and the latter holds live chat data. The same goes for the Telegram
session file the bot keeps next to the database (`database.session` beside `database.db`).

### Docker Usage

The [Dockerfile](Dockerfile) installs the bot into a virtual environment and ships it on a minimal Python image. It
reads its configuration from `/app/configuration.yml` and keeps its database and Telegram session under `/data`:

```shell
docker build -t spam-protection-bot .
docker run -d --name spam-protection-bot \
  -v "$(pwd)/configuration.yml:/app/configuration.yml:ro" \
  -v spb-data:/data \
  spam-protection-bot
```

The image sets `SPB_CONFIG=/app/configuration.yml` and `SPB_DATABASE=/data/database.db` as its defaults, so either
path can be moved with `-e`; mount the file or volume at the new location to match. Options given after the image
name, such as `--database /data/other.db`, still take precedence.

### Docker Compose Usage

Two compose files are provided, depending on whether you connect to an existing Federation server or host your own.

**Using an existing Federation server.** [docker-compose.instance.yml](docker-compose.instance.yml) runs only the
bot. Set `federation.endpoint` in `configuration.yml` to the server's base URL (prefer `https` for a server reached
over the internet, since the access token is sent with every request) and, optionally, `federation.access_token` to
an operator token issued by that server's administrator:

```shell
docker compose -f docker-compose.instance.yml up -d --build
```

**Hosting your own Federation server.** [docker-compose.yml](docker-compose.yml) spins up the whole stack: the bot, a
self-hosted [FederationLib](https://github.com/nosial/FederationLib) server, and FederationLib's required MariaDB
database and recommended Redis cache, wired together on an internal network so the bot reaches Federation at
`http://federation:7000/` without exposing it:

```shell
# in configuration.yml, set federation.endpoint to http://federation:7000/
docker compose up -d
```

Before running this anywhere but a local machine, change `FEDERATION_ACCESS_TOKEN` in `docker-compose.yml` (or
override it via a `.env` file) from its placeholder value.

The bot starts fine with no `federation.access_token` of its own, running anonymously against whatever the
Federation server allows without authentication, per [Federation Authentication](#federation-authentication). To run
it authenticated instead, create a dedicated operator with client permissions
(`docker compose exec federation federationlib create-operator --help` for the exact syntax), put that operator's
token in `configuration.yml`'s `federation.access_token`, and restart just the bot: `docker compose restart bot`.
Never reuse the `root` access of a Federation server with this bot.

### Command-Line Interface

| Option                    | Environment variable | Description                                                                       |
|---------------------------|----------------------|-----------------------------------------------------------------------------------|
| `-c`, `--config <path>`   | `SPB_CONFIG`         | Path to the YAML configuration file (default: `configuration.yml`)                |
| `-d`, `--database <path>` | `SPB_DATABASE`       | Path to the SQLite database file, created when missing (default: `./database.db`) |
| `-h`, `--help`            |                      | Show the usage text and exit                                                      |

The environment variables suit service managers and containers:

```shell
SPB_CONFIG=/etc/spb/configuration.yml SPB_DATABASE=/var/lib/spb/database.db spb
```

An option given on the command line takes precedence over its environment variable, and the variable takes
precedence over the default. A variable that is set but empty is treated as unset; one set to an invalid path stops
the bot from starting, with a message naming the variable.

## Configuration

SpamProtectionBot reads a single YAML file at startup and validates it: a property left out falls back to
its default value below, but a property that is present and invalid (wrong type, out of range, blank where text is
required) stops the bot from starting, with a message naming the offending field. Start from
[configuration.sample.yml](configuration.sample.yml), a fully annotated example:

```shell
cp configuration.sample.yml configuration.yml
```

At minimum, set `bot.name`, `bot.api_key`, `bot.api_id` and `bot.api_hash`, and, to enable spam protection,
`federation.endpoint`.

### Bot Configuration

The `bot` section is required.

| Name                                | Type   | Default                           | Required | Description                                                                                           |
|-------------------------------------|--------|-----------------------------------|----------|-------------------------------------------------------------------------------------------------------|
| `bot.name`                          | string |                                   | Yes      | Display name shown in the `/start` greeting                                                           |
| `bot.api_key`                       | string |                                   | Yes      | Telegram Bot API token issued by BotFather                                                            |
| `bot.api_id`                        | int    |                                   | Yes      | Telegram API ID issued at my.telegram.org; must be `>= 1`                                             |
| `bot.api_hash`                      | string |                                   | Yes      | Telegram API hash issued at my.telegram.org                                                           |
| `bot.test_server`                   | bool   | `false`                           | No       | Talk to Telegram's test server instead of the production one                                          |
| `bot.worker_threads`                | int    | number of CPU cores, at least `2` | No       | Concurrent tasks processing incoming updates; must be `>= 1`                                          |
| `bot.queue_capacity`                | int    | `1024`                            | No       | Maximum pending updates before surplus updates are dropped; must be `>= 1`                            |
| `bot.notification_interval_seconds` | int    | `60`                              | No       | Seconds between checks for newly assigned, open Federation reports; must be `>= 1`                    |
| `bot.default_language`              | string | `en`                              | No       | Default language code for user-facing text                                                            |
| `bot.privacy_mode`                  | bool   | `false`                           | No       | Default Privacy Mode setting for newly registered chats                                               |
| `bot.shutdown_timeout_seconds`      | int    | `30`                              | No       | How long shutdown waits for in-flight updates to finish before the queue is abandoned; must be `>= 1` |

The SQLite database location is deliberately not part of this file: it is a runtime location, not a bot setting,
and is chosen with `--database` or `SPB_DATABASE` instead (see
[Command-Line Interface](#command-line-interface)).

### Federation Configuration

The `federation` section is entirely optional. When it is absent, the bot starts and answers commands normally, but
every feature that depends on Federation (scanning, join protection, reporting, entity lookups) reports itself
unavailable rather than failing.

| Name                                  | Type   | Default | Required                         | Description                                                                                                             |
|---------------------------------------|--------|---------|----------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| `federation.endpoint`                 | string |         | Yes, when the section is present | Base URL of the Federation server                                                                                       |
| `federation.access_token`             | string |         | No                               | Access token the bot authenticates with; omit it to connect anonymously                                                 |
| `federation.web_application_endpoint` | string |         | No                               | Base URL of a FederationWeb instance for the same server; see [Federation Web Application](#federation-web-application) |

#### Federation Web Application

When `federation.web_application_endpoint` is set, every message that displays a Federation record (`/info`,
`/evidence`, `/report <uuid>`, `/blacklist`, report notifications and summaries, the operator `#REPORT_ASSIGNED`
notification and its evidence, scanning, join protection and secretary notifications, and the secretary contact
page) carries a button opening that record's page (`/reports/<uuid>`, `/evidence/<uuid>`, `/entities/<uuid>` or
`/blacklist/<uuid>`) in the web application. The button stays on a
notification after its action buttons are used up. Telegram rejects a message whose button URL it cannot open, so the
value must be a public `http` or `https` URL; a `localhost` or `127.0.0.1` address will cause those messages to fail.

### Federation Authentication

A Federation server treats an anonymous client and an authenticated one differently, and, importantly, treats a
*misconfigured* authenticated client worse than an anonymous one: several requests a server may allow anonymously
are refused outright to a token that identifies an operator without client permissions, since the server holds
authenticated requests to a stricter standard than anonymous ones. A token that does not clear that bar is
therefore worse than no token at all.

To avoid that trap, the bot checks its own token's permissions at startup. If `federation.access_token` is set but
the operator it belongs to lacks client permissions (or the token is rejected outright), the bot logs a warning and
continues anonymously for the rest of the run, exactly as if no token had been configured. When the server cannot be
reached at startup, the token is kept and checked again every 30 seconds until the server answers. You can see which
mode the bot ended up in from its startup log, or at any time with the `/ping` command, which reports Federation's
status alongside the bot's own.

A handful of features, most notably submitting a report with `/report`, have no anonymous path on the server at
all: they always need the bot's own token to carry client permissions, regardless of what the server otherwise
allows anonymously. When the bot is not authenticated, these features are omitted from `/settings` entirely rather
than shown and left to fail.

## Setting Up the Bot in Telegram

Once the bot is running, it can protect group chats, a personal inbox, or both. The two are set up independently.

### Group Protection

Add the bot to a group and promote it to administrator with, at minimum, the **Change Group Information**
permission, which is what lets it open its own configuration menu. **Delete Messages** and **Ban Users** are
optional but needed for scanning and join protection to take automated action rather than only notify. Once
promoted, send `/start` or `/settings` in the group to open the configuration menu and enable protection.

### Secretary Mode

Secretary Mode protects a personal inbox rather than a group. In Telegram, go to **Settings > Business > Chatbots**,
connect the bot, and grant it the **Read Messages** and **Delete Received Messages** permissions. Messages from
contacts you have not interacted with before are then checked against Federation and handled automatically
according to the behavior you choose in the Secretary Settings menu (`/settings` in a private chat).

The sender of a first-contact message is always checked, but the message itself is only sent to Federation for
scanning when **Scanning** is enabled in the Secretary Settings menu; it is off by default. When the bot is
authenticated, every first-contact notification has a **Report Message** button for reporting a message Federation
did not flag.

## Commands

The commands below are grouped by who uses them. Full syntax and explanations for every command are also available
from the bot itself via `/help`.

### General

| Command              | Description                                       |
|----------------------|---------------------------------------------------|
| `/help`              | Show the in-bot help menu, private chats only     |
| `/start`             | Add the bot to a group, or open your private menu |
| `/settings`          | Open the configuration menu                       |
| `/language`, `/lang` | Change your language                              |
| `/ping`              | Check the bot's and Federation server's status    |

### Chat Protection

| Command          | Description                                                                                                                                                                           |
|------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `/report`        | Reply to a message and send this to open the report dialog, or send `/report <type> [comment]` to submit directly. Forwarding a message to the bot privately reports it the same way. |
| `/report <uuid>` | Look up an existing report by its UUID                                                                                                                                                |
| `/connect <id>`  | Link a notification chat or channel, using the id shown on the Chat Linking settings page                                                                                             |

### Federation Lookups

Available in any chat; no authentication required.

| Command            | Description                                                                                                                                                                                                                    |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `/info [target]`   | Look up a Federation entity's record and recommendation. With no target, replying to a message looks up its author; sent plain, it looks up yourself. A target can also be a username, user id, entity address, UUID, or hash. |
| `/evidence <uuid>` | Look up a Federation evidence record by its UUID                                                                                                                                                                               |

### Federation Operators

These commands require signing in as a Federation operator first, and run with that operator's own permissions
rather than the bot's.

| Command                               | Description                                                                                                                                                                                                                                                 |
|---------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `/auth <access_token>`                | Sign in as a Federation operator with an access token, in a private chat with the bot                                                                                                                                                                       |
| `/authinfo`                           | Show your current operator identity and permissions                                                                                                                                                                                                         |
| `/deauth`                             | Sign out                                                                                                                                                                                                                                                    |
| `/blacklist <type> <expires>` (reply) | Create a supporting report for the replied-to message's author, then blacklist them. Give an existing `<report_uuid>` before the type to blacklist against it instead, or the full `<identifier> <report_uuid> <type> <expires>` to skip the reply entirely |
| `/link <entity> <target> <type>`      | Record a relationship (`alternative`, `proxy`, or `child`) between two Federation entities                                                                                                                                                                  |

## Adding a Language

Every user-facing string is served from a YAML file under `src/spb/resources/languages/`, discovered in the
package at startup, so there is no code change involved in adding one. A translation file is a nested mapping
flattened into dotted keys at lookup time, and must declare a `localization_properties` section naming the language:

```yaml
localization_properties:
  name: "Español"
  emoji: "🇪🇸"

general:
  "yes": "Sí"
  "no": "No"
  # ...
```

Copy `en.yml` as a starting point, translate its values, and name the new file after the language's code (e.g.
`es.yml`). A key missing from a language falls back to the default language, so a partial translation degrades
gracefully rather than breaking. `bot.default_language` in the configuration file selects which language is used
when a user or chat has not chosen one.

# License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
