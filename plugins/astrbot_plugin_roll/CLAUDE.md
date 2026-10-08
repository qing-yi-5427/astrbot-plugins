# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AstrBot plugin that provides a `/roll` command for random selection — either from a list of options (`/roll A B C`) or from a numeric range (`/roll 1-100`). Written in Chinese (UI strings, comments, metadata).

## Architecture

Single-file plugin (`main.py`) built on the AstrBot plugin framework. The plugin class extends `Star` and uses two key decorators:

- `@register(...)` — declares plugin metadata (name, author, description, version)
- `@filter.command("roll")` — registers the `/roll` slash command handler

Commands return results via **async generators** (`yield event.chain_result(...)`) rather than `return`. Plugin lifecycle hooks (`initialize`, `terminate`) are async methods called by the framework.

## AstrBot Plugin Conventions

- Plugin metadata lives in `metadata.yaml` (name, display_name, desc, version, author, astrbot_version)
- Message components are accessed via `astrbot.api.message_components` (aliased as `Comp`)
- `event.message_str` contains the raw message text; options are space-split from it
- Results are yielded as `event.chain_result([Comp.Plain(...)])`

## Requirements

- AstrBot >= 4.16.0
- No third-party dependencies (stdlib only: `random`)
- Python 3 with async/await support

## Installation Path

Plugins are installed to `AstrBot/data/plugins/astrbot_plugin_roll/`.
